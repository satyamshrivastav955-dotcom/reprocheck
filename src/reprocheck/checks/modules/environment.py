"""Static environment template and documentation check."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

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
    unsupported_finding,
)

ENV_SPEC = CheckSpec(
    check_id="RC-ENV-001",
    name="Environment template or documentation",
    category=CheckCategory.DOCUMENTATION,
    verification_class=VerificationClass.STATIC,
    description=(
        "Find a non-secret environment template or conservative documentation of "
        "environment configuration."
    ),
    severity=Severity.LOW,
    supported_ecosystems=ALL_SUPPORTED_ECOSYSTEMS,
    limitations=(
        "The check does not infer required variables from application code.",
        "Required-variable declarations are recognized in Compose files and explicit README lists.",
        "It does not validate values or connectivity.",
    ),
    false_positive_policy=(
        "Require documented variable names or explicit no-required-env documentation; "
        "absence of a template alone does not establish a defect."
    ),
    scoring_group="environment",
)

_TEMPLATE_NAMES = {".env.example", ".env.template", ".env.sample", "env.example", "env.template"}
_DOCUMENTATION_RE = re.compile(
    r"(?i)(?:environment\s+variables?|configuration\s+variables?|\.env\b)"
)
_VARIABLE_RE = re.compile(r"\b([A-Z_][A-Z0-9_]*)\b`?[ \t]*(?:=|:)[ \t]*\S+")
_TEMPLATE_VARIABLE_RE = re.compile(r"(?m)^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_REQUIRED_VARIABLE_RE = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*):?\?")
_README_REQUIRED_RE = re.compile(
    r"(?im)^[ \t]*(?:[-*][ \t]+)?required[ \t]+environment[ \t]+variables?[ \t]*:([^\n]+)"
)
_NO_REQUIRED_RE = re.compile(
    r"(?im)\b(?:no\s+(?:required\s+)?environment\s+variables?"
    r"(?:\s+(?:are\s+)?required)?|"
    r"(?:does\s+not|doesn't)\s+require\s+(?:any\s+)?environment\s+variables?|"
    r"environment\s+variables?\s+are\s+not\s+required)\s*(?:[.!]|$)"
)


def _readme_paths(context: CheckContext) -> tuple[str, ...]:
    return tuple(
        sorted(
            repo_file.path
            for repo_file in context.discovery.files
            if not repo_file.is_symlink
            and PurePosixPath(repo_file.path).name.lower()
            in {"readme", "readme.md", "readme.rst", "readme.txt"}
        )
    )


class EnvironmentDocumentationCheck(BaseCheck):
    spec = ENV_SPEC

    def evaluate(self, context: CheckContext) -> tuple[Finding, ...]:
        inputs = {path: "documentation" for path in _readme_paths(context)}
        for repo_file in context.discovery.files:
            if repo_file.is_symlink:
                continue
            relative = PurePosixPath(repo_file.path)
            name = relative.name.lower()
            if len(relative.parts) == 1 and name in _TEMPLATE_NAMES:
                inputs[repo_file.path] = "template"
            elif name.endswith((".yml", ".yaml")) and (
                name.startswith("docker-compose.") or name.startswith("compose.")
            ):
                inputs[repo_file.path] = "requirements"

        required: set[str] = set()
        documented: set[str] = set()
        template_evidence = []
        documentation_evidence = []
        requirement_evidence = []
        no_required_evidence = []
        for path, kind in sorted(inputs.items()):
            result = read_known_text(context, path)
            if result.status != "ok":
                if result.status == "inaccessible":
                    return (
                        blocked_finding(
                            self.spec,
                            title="Environment evidence is inaccessible",
                            message=f"The environment {kind} file {path} could not be read.",
                            reason=result.error or "environment evidence is inaccessible",
                            path=path,
                        ),
                    )
                return (
                    parse_error_finding(
                        self.spec,
                        title="Environment evidence cannot be assessed",
                        message=f"The environment {kind} file {path} could not be read safely.",
                        reason=result.error or result.status,
                        path=path,
                    ),
                )
            if kind == "requirements":
                text = "\n".join(
                    re.split(r"\s+#", line, maxsplit=1)[0]
                    for line in result.text.splitlines()
                    if not line.lstrip().startswith("#")
                )
                names = set(_REQUIRED_VARIABLE_RE.findall(text))
                required.update(names)
                if names:
                    requirement_evidence.append(
                        static_evidence(
                            self.spec,
                            "Explicit required environment variables detected.",
                            source=path,
                            details={"required_variables": sorted(names)},
                        )
                    )
            elif kind == "template":
                names = set(_TEMPLATE_VARIABLE_RE.findall(result.text))
                documented.update(names)
                if names:
                    template_evidence.append(
                        static_evidence(self.spec, "Environment template detected.", source=path)
                    )
            else:
                declared = {
                    name
                    for match in _README_REQUIRED_RE.finditer(result.text)
                    for name in re.findall(r"\b[A-Z_][A-Z0-9_]*\b", match[1])
                }
                required.update(declared)
                if declared:
                    requirement_evidence.append(
                        static_evidence(
                            self.spec,
                            "README explicitly declares required environment variables.",
                            source=path,
                            details={"required_variables": sorted(declared)},
                        )
                    )
                if _NO_REQUIRED_RE.search(result.text):
                    no_required_evidence.append(
                        static_evidence(
                            self.spec,
                            "README explicitly documents no required environment variables.",
                            source=path,
                        )
                    )
                if _DOCUMENTATION_RE.search(result.text):
                    names = set(_VARIABLE_RE.findall(result.text))
                    documented.update(names)
                    if names:
                        documentation_evidence.append(
                            static_evidence(
                                self.spec, "Environment variables are documented.", source=path
                            )
                        )

        missing = sorted(required - documented)
        if missing:
            return (
                make_finding(
                    self.spec,
                    title="Required environment variables are undocumented",
                    message=(
                        "Explicitly required variables are missing from templates and README docs."
                    ),
                    assessment=Assessment.FAIL,
                    state=ResultState.STATIC_FAIL,
                    evidence=(
                        static_evidence(
                            self.spec,
                            "Required variables lack template or documentation entries.",
                            details={"missing_variables": list(missing)},
                        ),
                        *requirement_evidence,
                    ),
                    remediation=(
                        "Document each required variable or add it to a redacted .env.example."
                    ),
                ),
            )
        if template_evidence:
            return (
                make_finding(
                    self.spec,
                    title="Environment template detected",
                    message="A conventional template names environment configuration variables.",
                    assessment=Assessment.INFO,
                    state=ResultState.PRESENT,
                    evidence=tuple(
                        template_evidence + documentation_evidence + requirement_evidence
                    ),
                ),
            )
        if documentation_evidence or no_required_evidence:
            return (
                make_finding(
                    self.spec,
                    title="Environment requirements are documented",
                    message=(
                        "README text documents environment variables or explicitly states "
                        "that none are required."
                    ),
                    assessment=Assessment.PASS,
                    state=ResultState.LOOKS_CORRECT,
                    evidence=tuple(
                        documentation_evidence + no_required_evidence + requirement_evidence
                    ),
                ),
            )
        return (
            unsupported_finding(
                self.spec,
                title="Environment documentation applicability is unknown",
                message=(
                    "No required environment configuration or substantive documentation was found."
                ),
                reason=(
                    "No explicit required-variable declarations, documented variables, or "
                    "no-required-env statement was discovered; a missing template alone "
                    "does not establish a defect."
                ),
            ),
        )


__all__ = ["ENV_SPEC", "EnvironmentDocumentationCheck"]
