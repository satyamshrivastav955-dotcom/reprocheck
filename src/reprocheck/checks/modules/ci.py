"""Static CI setup and test workflow heuristics without a YAML dependency."""

from __future__ import annotations

import re

from ...command_analysis import (
    MAKE_DEFAULT_POLICY,
    analyze_command,
    is_setup_command,
    workflow_jobs,
)
from ...models import (
    Assessment,
    CheckCategory,
    CheckSpec,
    Finding,
    ResultState,
    Severity,
    VerificationClass,
)
from ..base import (
    ALL_SUPPORTED_ECOSYSTEMS,
    BaseCheck,
    CheckContext,
    blocked_finding,
    make_finding,
    parse_error_finding,
    read_known_text,
    static_evidence,
)

CI_SPEC = CheckSpec(
    check_id="RC-CI-001",
    name="CI setup and test workflow",
    category=CheckCategory.TESTS,
    verification_class=VerificationClass.STATIC,
    description=(
        "Inspect common CI configuration for setup/install and test steps using "
        "conservative YAML shape heuristics."
    ),
    severity=Severity.MEDIUM,
    supported_ecosystems=ALL_SUPPORTED_ECOSYSTEMS,
    limitations=(
        "YAML is not fully parsed because the static engine has no third-party "
        "runtime dependencies.",
        "Provider-specific extensions may be reported as not verified.",
        "Only literal root Makefile targets, prerequisites and recipes are followed.",
        "Dynamic Make variables/includes, shell programs and custom job defaults remain uncertain.",
        MAKE_DEFAULT_POLICY,
    ),
    false_positive_policy=(
        "Require recognizable workflow structure and test intent before reporting a pass."
    ),
    scoring_group="ci",
)


def _workflow_paths(context: CheckContext) -> tuple[str, ...]:
    result: list[str] = []
    for repo_file in context.discovery.files:
        if repo_file.is_symlink:
            continue
        path = repo_file.path.lower()
        if path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml")):
            result.append(repo_file.path)
        elif path in {".gitlab-ci.yml", ".gitlab-ci.yaml", ".travis.yml", "azure-pipelines.yml"}:
            result.append(repo_file.path)
        elif path.startswith(".circleci/") and path.endswith((".yml", ".yaml")):
            result.append(repo_file.path)
    return tuple(sorted(result))


def _basic_yaml_shape(text: str) -> tuple[bool, str | None]:
    if not text.strip():
        return False, "workflow is empty"
    if "\x00" in text:
        return False, "workflow contains NUL bytes"
    if any(line.startswith("\t") for line in text.splitlines()):
        return False, "workflow uses tab indentation"
    if not re.search(r"(?m)^\s*(?:jobs|stages|steps|script)\s*:", text):
        return False, "workflow has no recognizable jobs, stages, steps, or script key"
    return True, None


def _workflow_assessment(
    text: str, path: str, context: CheckContext | None = None
) -> tuple[str, str | None, str | None]:
    shape_ok, shape_error = _basic_yaml_shape(text)
    if not shape_ok:
        return "malformed", shape_error, None
    normalized_path = path.lower()
    is_github = normalized_path.startswith(".github/workflows/")
    if is_github:
        required_shape = (
            re.search(r"(?m)^\s*jobs\s*:", text),
            re.search(r"(?m)^\s*[-\w]+\s*:\s*$", text),
            re.search(r"(?m)^\s+steps\s*:", text),
            re.search(r"(?m)^\s+runs-on\s*:", text),
        )
        if not all(required_shape):
            return (
                "malformed",
                "GitHub workflow is missing jobs, job steps, or runs-on structure",
                None,
            )
    else:
        if not re.search(r"(?m)^\s*(?:script|before_script|stages)\s*:", text):
            return "malformed", "CI file has no recognizable script/stages structure", None

    uncertainty: list[str] = []
    for job in workflow_jobs(text, path):
        job_reason = job.reason or (None if job.runner else "job has no literal runner scope")
        setup_lines = list(job.setup)
        if job_reason:
            uncertainty.append(f"job {job.name}: {job_reason}")
        for command in job.commands:
            analysis = analyze_command(command.raw, context)
            if analysis.reason:
                uncertainty.append(f"job {job.name}, line {command.line}: {analysis.reason}")
                continue
            lines = command.raw.splitlines()
            for offset, invocation in enumerate(lines):
                number = command.line + offset
                if is_setup_command(invocation):
                    setup_lines.append(number)
                line_analysis = (
                    analysis if len(lines) == 1 else analyze_command(invocation, context)
                )
                tests = [leaf for leaf in line_analysis.leaves if leaf.intent == "test"]
                if tests and any(line < number for line in setup_lines) and not job_reason:
                    evidence = [f"job {job.name}, line {number}: {invocation}"]
                    evidence.extend(
                        f"{leaf.source}:{leaf.line}, target {leaf.target}: {leaf.raw}"
                        for leaf in tests
                        if leaf.source
                    )
                    defaults = sorted({entry for leaf in tests for entry in leaf.make_defaults})
                    if defaults:
                        evidence.append(
                            "declared defaults "
                            + ", ".join(f"{name}={value}" for name, value in defaults)
                        )
                    return "valid", MAKE_DEFAULT_POLICY if defaults else None, " -> ".join(evidence)
    if uncertainty:
        return "uncertain", "; ".join(dict.fromkeys(uncertainty)), None
    return "incomplete", None, None


class CiWorkflowCheck(BaseCheck):
    spec = CI_SPEC

    def evaluate(self, context: CheckContext) -> tuple[Finding, ...]:
        paths = _workflow_paths(context)
        if not paths:
            return (
                make_finding(
                    self.spec,
                    title="CI setup/test workflow is missing",
                    message="No recognized CI workflow file was discovered.",
                    assessment=Assessment.FAIL,
                    state=ResultState.STATIC_FAIL,
                    evidence=(
                        static_evidence(
                            self.spec, "No recognized CI workflow file was discovered."
                        ),
                    ),
                    remediation=(
                        "Add a CI workflow that installs project dependencies and runs "
                        "the documented test command."
                    ),
                ),
            )

        observations: list[tuple[str, str, bool]] = []
        uncertainties: list[tuple[str, str]] = []
        default_policies: dict[str, str] = {}
        for path in paths:
            result = read_known_text(context, path)
            if result.status != "ok":
                if result.status == "inaccessible":
                    return (
                        blocked_finding(
                            self.spec,
                            title="CI workflow is inaccessible",
                            message=f"The CI workflow {path} could not be read.",
                            reason=result.error or "workflow is inaccessible",
                            path=path,
                        ),
                    )
                return (
                    parse_error_finding(
                        self.spec,
                        title="CI workflow cannot be assessed",
                        message=f"The CI workflow {path} could not be read safely.",
                        reason=result.error or result.status,
                        path=path,
                    ),
                )
            outcome, reason, excerpt = _workflow_assessment(result.text, path, context)
            if outcome == "malformed":
                return (
                    parse_error_finding(
                        self.spec,
                        title="CI workflow syntax is not statically understood",
                        message=(
                            f"The CI workflow {path} does not match the bounded "
                            "workflow syntax heuristics."
                        ),
                        reason=reason or "unknown workflow shape",
                        path=path,
                    ),
                )
            if outcome == "uncertain":
                uncertainties.append((path, reason or "workflow command is unresolved"))
            if outcome == "valid" and reason:
                default_policies[path] = reason
            observations.append((path, excerpt or "", outcome == "valid"))

        valid_paths = {path for path, _, is_valid in observations if is_valid}
        if not valid_paths:
            if uncertainties:
                return (
                    make_finding(
                        self.spec,
                        title="CI setup/test coverage is not verifiable",
                        message="Workflow commands or scopes exceed bounded literal analysis.",
                        assessment=Assessment.UNSUPPORTED,
                        state=ResultState.NOT_VERIFIED,
                        evidence=tuple(
                            static_evidence(
                                self.spec,
                                "CI workflow contains unresolved commands.",
                                source=path,
                                excerpt=reason,
                            )
                            for path, reason in uncertainties
                        ),
                        applicability_reason="Unsupported workflow or Makefile syntax.",
                    ),
                )
            return (
                make_finding(
                    self.spec,
                    title="CI workflow lacks setup or test coverage",
                    message=(
                        "Recognized CI configuration exists, but no workflow has both "
                        "runtime/dependency setup and a conservative test command in one job."
                    ),
                    assessment=Assessment.FAIL,
                    state=ResultState.STATIC_FAIL,
                    evidence=tuple(
                        static_evidence(self.spec, "CI workflow inspected.", source=path)
                        for path in paths
                    ),
                    remediation=(
                        "Add dependency setup and a recognized test step to the CI workflow."
                    ),
                ),
            )
        evidence = tuple(
            static_evidence(
                self.spec,
                "CI workflow has setup and a recognized test command.",
                source=path,
                excerpt=excerpt,
                details={"make_default_policy": default_policies[path]}
                if path in default_policies
                else None,
            )
            for path, excerpt, _ in observations
            if path in valid_paths
        )
        return (
            make_finding(
                self.spec,
                title="CI setup/test workflow detected",
                message=(
                    "At least one recognized CI workflow contains setup and a conservative "
                    "test command."
                ),
                assessment=Assessment.PASS,
                state=ResultState.LOOKS_CORRECT,
                evidence=evidence,
            ),
        )


__all__ = ["CI_SPEC", "CiWorkflowCheck"]
