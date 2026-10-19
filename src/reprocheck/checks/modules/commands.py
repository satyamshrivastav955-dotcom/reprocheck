"""Conservative command discoverability and consistency checks."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
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
    top_level_paths,
    unsupported_finding,
)


@dataclass(frozen=True)
class CommandObservation:
    source: str
    intents: frozenset[str]
    families: frozenset[str]
    excerpt: str


_TEST_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(?:python(?:\s+-m)?\s+)?(?:pytest|tox|nox)\b", "python"),
    (r"\b(?:npm|yarn|pnpm)\s+(?:run\s+)?test\b", "node"),
    (r"\bcargo\s+test\b", "rust"),
    (r"\bgo\s+test\b", "go"),
    (r"\bdotnet\s+test\b", "dotnet"),
    (r"\b(?:mvn|gradle)\s+test\b", "java"),
    (r"\bmake\s+test\b", "script"),
)
_BUILD_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(?:python\s+-m\s+build|uv\s+build)\b", "python"),
    (r"\b(?:npm|yarn|pnpm)\s+(?:run\s+)?build\b", "node"),
    (r"\bcargo\s+build\b", "rust"),
    (r"\bgo\s+build\b", "go"),
    (r"\bdotnet\s+build\b", "dotnet"),
    (r"\b(?:mvn\s+package|gradle\s+build)\b", "java"),
    (r"\bmake\s+build\b", "script"),
    (r"\bdocker\s+build\b", "container"),
)


def classify_command_text(text: str) -> tuple[frozenset[str], frozenset[str], str]:
    """Classify known test/build command forms without parsing or executing shell."""

    intents: set[str] = set()
    families: set[str] = set()
    for pattern, family in _TEST_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            intents.add("test")
            families.add(family)
    for pattern, family in _BUILD_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            intents.add("build")
            families.add(family)
    return frozenset(intents), frozenset(families), text.strip()[:400]


def _script_intent(name: str) -> str | None:
    normalized = name.lower().split(":", 1)[0]
    if normalized in {"test", "check", "lint", "typecheck"}:
        return "test"
    if normalized == "build":
        return "build"
    return None


def _package_script_observations(path: str, text: str) -> tuple[CommandObservation, ...]:
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("package manifest is not an object")
    scripts = parsed.get("scripts", {})
    if not isinstance(scripts, dict):
        raise ValueError("package.json scripts is not an object")
    observations: list[CommandObservation] = []
    for name, command in sorted(scripts.items(), key=lambda item: str(item[0])):
        if not isinstance(name, str) or not isinstance(command, str):
            raise ValueError("package.json script names and commands must be strings")
        intent = _script_intent(name)
        if intent is not None:
            observations.append(
                CommandObservation(
                    path, frozenset({intent}), frozenset({"script"}), f"{name}: {command}"
                )
            )
    return tuple(observations)


def _toml_script_observations(path: str, text: str) -> tuple[CommandObservation, ...]:
    parsed = tomllib.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("TOML document is not an object")
    tables: list[object] = []
    project = parsed.get("project")
    if isinstance(project, dict):
        tables.append(project.get("scripts", {}))
    tool = parsed.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict):
            tables.append(poetry.get("scripts", {}))
    observations: list[CommandObservation] = []
    for scripts in tables:
        if not isinstance(scripts, dict):
            raise ValueError("Python project scripts is not a table")
        for name, command in sorted(scripts.items(), key=lambda item: str(item[0])):
            if not isinstance(name, str) or not isinstance(command, str):
                raise ValueError("Python project script names and commands must be strings")
            intent = _script_intent(name)
            if intent is not None:
                observations.append(
                    CommandObservation(
                        path, frozenset({intent}), frozenset({"script"}), f"{name}: {command}"
                    )
                )
    return tuple(observations)


def _is_ci_path(path: str) -> bool:
    normalized = path.lower()
    return (
        normalized.startswith(".github/workflows/")
        or normalized in {".gitlab-ci.yml", ".gitlab-ci.yaml", ".travis.yml", "azure-pipelines.yml"}
        or normalized.startswith(".circleci/")
    ) and normalized.endswith((".yml", ".yaml"))


def command_observations(
    context: CheckContext,
) -> tuple[tuple[CommandObservation, ...], tuple[tuple[str, str, str], ...]]:
    """Collect bounded command observations and non-passable read/parse errors."""

    observations: list[CommandObservation] = []
    errors: list[tuple[str, str, str]] = []
    paths = set(top_level_paths(context))
    for path in sorted(paths):
        name = PurePosixPath(path).name.lower()
        if name not in {"package.json", "pyproject.toml"}:
            continue
        result = read_known_text(context, path)
        if result.status != "ok":
            errors.append((path, result.error or result.status, result.status))
            continue
        try:
            parsed = (
                _package_script_observations(path, result.text)
                if name == "package.json"
                else _toml_script_observations(path, result.text)
            )
        except (ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
            errors.append((path, str(error), "malformed"))
        else:
            observations.extend(parsed)

    for repo_file in sorted(
        (
            repo_file
            for repo_file in context.discovery.files
            if _is_ci_path(repo_file.path) and not repo_file.is_symlink
        ),
        key=lambda repo_file: repo_file.path,
    ):
        result = read_known_text(context, repo_file.path)
        if result.status != "ok":
            errors.append((repo_file.path, result.error or result.status, result.status))
            continue
        intents, families, excerpt = classify_command_text(result.text)
        if intents:
            observations.append(CommandObservation(repo_file.path, intents, families, excerpt))

    for repo_file in sorted(
        (
            repo_file
            for repo_file in context.discovery.files
            if PurePosixPath(repo_file.path).name.lower()
            in {"readme", "readme.md", "readme.rst", "readme.txt"}
            and len(PurePosixPath(repo_file.path).parts) == 1
            and not repo_file.is_symlink
        ),
        key=lambda repo_file: repo_file.path,
    ):
        result = read_known_text(context, repo_file.path)
        if result.status != "ok":
            errors.append((repo_file.path, result.error or result.status, result.status))
            continue
        intents, families, excerpt = classify_command_text(result.text)
        if intents:
            observations.append(CommandObservation(repo_file.path, intents, families, excerpt))

    return tuple(observations), tuple(errors)


COMMAND_SPEC = CheckSpec(
    check_id="RC-CMD-001",
    name="Test/build command discoverability",
    category=CheckCategory.TESTS,
    verification_class=VerificationClass.STATIC,
    description=(
        "Find conservative test or build command forms in package scripts, Python "
        "scripts, CI, or README text."
    ),
    severity=Severity.MEDIUM,
    supported_ecosystems=ALL_SUPPORTED_ECOSYSTEMS,
    limitations=(
        "Commands are classified by patterns only.",
        "No command is parsed as an executable shell program.",
    ),
    false_positive_policy=(
        "A named script or recognized command is evidence of discoverability, not "
        "proof it succeeds."
    ),
    scoring_group="commands",
)


class CommandDiscoverabilityCheck(BaseCheck):
    spec = COMMAND_SPEC

    def evaluate(self, context: CheckContext) -> tuple[Finding, ...]:
        observations, errors = command_observations(context)
        if errors:
            path, reason, status = errors[0]
            if status == "inaccessible":
                return (
                    blocked_finding(
                        self.spec,
                        title="Command discoverability is blocked",
                        message=f"A command source {path} could not be read.",
                        reason=reason,
                        path=path,
                    ),
                )
            return (
                parse_error_finding(
                    self.spec,
                    title="Command discoverability cannot be assessed",
                    message=f"A command source {path} could not be parsed safely.",
                    reason=reason,
                    path=path,
                ),
            )
        if not observations:
            return (
                make_finding(
                    self.spec,
                    title="Test/build command is not discoverable",
                    message=(
                        "No conservative test or build command was found in supported "
                        "static sources."
                    ),
                    assessment=Assessment.FAIL,
                    state=ResultState.STATIC_FAIL,
                    evidence=(
                        static_evidence(self.spec, "No recognized test/build command was found."),
                    ),
                    remediation=(
                        "Document or expose a test/build command in package scripts, "
                        "project scripts, CI, or README."
                    ),
                ),
            )
        evidence = tuple(
            static_evidence(
                self.spec,
                "Recognized test/build command source detected.",
                source=observation.source,
                excerpt=observation.excerpt,
                details={"intents": [intent_value for intent_value in sorted(observation.intents)]},
            )
            for observation in observations
        )
        return (
            make_finding(
                self.spec,
                title="Test/build command is discoverable",
                message=(
                    "At least one conservative test or build command is exposed by "
                    "repository documentation or metadata."
                ),
                assessment=Assessment.PASS,
                state=ResultState.LOOKS_CORRECT,
                evidence=evidence,
            ),
        )


CONSISTENCY_SPEC = CheckSpec(
    check_id="RC-CMD-002",
    name="README/package/CI command consistency",
    category=CheckCategory.DOCUMENTATION,
    verification_class=VerificationClass.STATIC,
    description=(
        "Compare recognized test/build intents and concrete command families across "
        "package metadata, CI, and README text."
    ),
    severity=Severity.MEDIUM,
    supported_ecosystems=ALL_SUPPORTED_ECOSYSTEMS,
    limitations=(
        "Wrapper scripts are treated as compatible with one concrete command family.",
        "Absence in one source is not treated as a contradiction.",
    ),
    false_positive_policy=(
        "Report only conflicts between two concrete command families for the same intent."
    ),
    scoring_group="commands",
)


class CommandConsistencyCheck(BaseCheck):
    spec = CONSISTENCY_SPEC

    def evaluate(self, context: CheckContext) -> tuple[Finding, ...]:
        observations, errors = command_observations(context)
        if errors:
            path, reason, status = errors[0]
            if status == "inaccessible":
                return (
                    blocked_finding(
                        self.spec,
                        title="Command consistency is blocked",
                        message=f"A command source {path} could not be read.",
                        reason=reason,
                        path=path,
                    ),
                )
            return (
                parse_error_finding(
                    self.spec,
                    title="Command consistency cannot be assessed",
                    message=f"A command source {path} could not be parsed safely.",
                    reason=reason,
                    path=path,
                ),
            )
        by_intent: dict[str, list[CommandObservation]] = {"test": [], "build": []}
        for observation in observations:
            for intent in observation.intents:
                by_intent.setdefault(intent, []).append(observation)
        comparable = [
            items for items in by_intent.values() if len({item.source for item in items}) >= 2
        ]
        if not comparable:
            return (
                unsupported_finding(
                    self.spec,
                    title="Command consistency is not verifiable",
                    message=(
                        "Fewer than two independent command sources expose the same "
                        "test/build intent."
                    ),
                    reason=(
                        "Consistency requires at least two independently documented "
                        "command sources."
                    ),
                ),
            )
        conflicts: list[tuple[str, tuple[str, ...]]] = []
        for intent, items in by_intent.items():
            families = {family for item in items for family in item.families if family != "script"}
            if len(families) > 1:
                conflicts.append((intent, tuple(sorted(families))))
        evidence = tuple(
            static_evidence(
                self.spec,
                "Recognized command source used for consistency comparison.",
                source=observation.source,
                excerpt=observation.excerpt,
                details={
                    "intents": [intent_value for intent_value in sorted(observation.intents)],
                    "families": [family_value for family_value in sorted(observation.families)],
                },
            )
            for observation in observations
        )
        if conflicts:
            return (
                make_finding(
                    self.spec,
                    title="Documented test/build commands are inconsistent",
                    message=(
                        "Independent sources use different concrete command families "
                        "for the same intent."
                    ),
                    assessment=Assessment.WARN,
                    state=ResultState.STATIC_FAIL,
                    evidence=evidence,
                    remediation=(
                        "Align README, package/project scripts, and CI around one "
                        "documented command family."
                    ),
                    code="command-family-conflict",
                ),
            )
        return (
            make_finding(
                self.spec,
                title="Documented test/build commands are consistent",
                message="Independent command sources expose compatible test/build intents.",
                assessment=Assessment.PASS,
                state=ResultState.LOOKS_CORRECT,
                evidence=evidence,
            ),
        )


__all__ = [
    "COMMAND_SPEC",
    "CONSISTENCY_SPEC",
    "CommandConsistencyCheck",
    "CommandDiscoverabilityCheck",
    "CommandObservation",
    "classify_command_text",
    "command_observations",
]
