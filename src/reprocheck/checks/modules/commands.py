"""Conservative command discoverability and consistency checks."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import PurePosixPath

from ...command_analysis import (
    MAKE_DEFAULT_POLICY,
    CommandLeaf,
    CommandLine,
    analyze_command,
    literal_tokens,
    readme_command_lines,
    workflow_jobs,
)
from ...models import (
    Assessment,
    CheckCategory,
    CheckSpec,
    Finding,
    JSONValue,
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
    redact_excerpt,
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
    line: int | None = None
    commands: tuple[tuple[str, ...], ...] = ()
    resolved: tuple[CommandLeaf, ...] = ()
    reason: str | None = None


def classify_command_text(text: str) -> tuple[frozenset[str], frozenset[str], str]:
    """Classify literal command lines; arbitrary prose is not command evidence."""
    leaves = analyze_command(text).leaves
    return (
        frozenset(leaf.intent for leaf in leaves),
        frozenset(leaf.family for leaf in leaves),
        text.strip()[:400],
    )


def _line_observations(
    path: str,
    command: CommandLine,
    context: CheckContext,
    wrappers: dict[str, tuple[CommandLeaf, ...]],
    reason: str | None = None,
) -> tuple[CommandObservation, ...]:
    analysis = analyze_command(command.raw, context)
    leaves = tuple(
        resolved
        for leaf in analysis.leaves
        for resolved in (
            wrappers.get(leaf.identity[1], (leaf,))
            if leaf.family == "node" and len(leaf.identity) == 2
            else (leaf,)
        )
    )
    intents = {leaf.intent for leaf in leaves}
    if not intents and analysis.reason:
        # Retain unresolved *invocations*, not mentions of command words in prose.
        if re.match(
            r"^(?:make\b|(?:uv run(?: --\w+)* )?"
            r"(?:pytest|tox|nox|python3? -m (?:pytest|unittest))\b)",
            command.raw,
        ):
            intents.add("build" if re.match(r"make\s+build\b", command.raw) else "test")
    result: list[CommandObservation] = []
    for intent in sorted(intents):
        relevant = tuple(leaf for leaf in leaves if leaf.intent == intent)
        result.append(
            CommandObservation(
                path,
                frozenset({intent}),
                frozenset(leaf.family for leaf in relevant),
                command.raw.strip()[:400],
                command.line,
                tuple(leaf.identity for leaf in relevant),
                relevant,
                reason or analysis.reason,
            )
        )
    return tuple(result)


def _observation_details(observation: CommandObservation) -> dict[str, JSONValue]:
    details: dict[str, JSONValue] = {
        "intents": [intent for intent in sorted(observation.intents)],
        "families": [family for family in sorted(observation.families)],
        "commands": [
            [redact_excerpt(value) for value in command] for command in observation.commands
        ],
    }
    if observation.line is not None:
        details["line"] = observation.line
    if observation.reason:
        details["reason"] = redact_excerpt(observation.reason)
    if any(leaf.source for leaf in observation.resolved):
        details["resolved"] = [
            {
                "source": leaf.source,
                "line": leaf.line,
                "target": leaf.target,
                "command": redact_excerpt(leaf.raw),
                "make_defaults": {
                    name: redact_excerpt(value) for name, value in leaf.make_defaults
                },
            }
            for leaf in observation.resolved
            if leaf.source
        ]
    if any(leaf.make_defaults for leaf in observation.resolved):
        details["make_default_policy"] = MAKE_DEFAULT_POLICY
    return details


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
    wrappers: dict[str, tuple[CommandLeaf, ...]] = {}
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
            for observation in parsed:
                name, _, raw = observation.excerpt.partition(": ")
                analysis = analyze_command(raw, context)
                leaves = tuple(
                    leaf for leaf in analysis.leaves if leaf.intent in observation.intents
                )
                if leaves and not analysis.reason:
                    resolved = tuple(
                        CommandLeaf(
                            leaf.intent,
                            leaf.family,
                            leaf.identity,
                            leaf.raw,
                            path,
                            target=name,
                            make_defaults=leaf.make_defaults,
                        )
                        for leaf in leaves
                    )
                    observations.append(
                        CommandObservation(
                            path,
                            observation.intents,
                            frozenset(leaf.family for leaf in leaves),
                            observation.excerpt,
                            commands=tuple(leaf.identity for leaf in leaves),
                            resolved=resolved,
                        )
                    )
                    if (
                        name
                        and name in {"test", "build", "check", "lint", "typecheck"}
                        and path == "package.json"
                    ):
                        wrappers[name] = resolved
                else:
                    observations.append(
                        CommandObservation(
                            observation.source,
                            observation.intents,
                            observation.families,
                            observation.excerpt,
                            reason=analysis.reason
                            if (raw.strip().startswith("make ") or literal_tokens(raw) is None)
                            else None,
                        )
                    )

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
        for job in workflow_jobs(result.text, repo_file.path):
            for command in job.commands:
                observations.extend(
                    _line_observations(repo_file.path, command, context, wrappers, job.reason)
                )

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
        for command in readme_command_lines(result.text):
            observations.extend(_line_observations(repo_file.path, command, context, wrappers))

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
        "Only bounded literal commands and root Makefile rules are understood.",
        "Shell syntax, dynamic Make variables/includes and dynamic workflows are not resolved.",
        MAKE_DEFAULT_POLICY,
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
        certain = [observation for observation in observations if not observation.reason]
        if not certain:
            return (
                unsupported_finding(
                    self.spec,
                    title="Command discoverability is not verifiable",
                    message="Command invocations exist, but their concrete intent is unresolved.",
                    reason="; ".join(dict.fromkeys(item.reason or "" for item in observations)),
                ),
            )
        evidence = tuple(
            static_evidence(
                self.spec,
                "Recognized test/build command source detected.",
                source=observation.source,
                excerpt=observation.excerpt,
                details=_observation_details(observation),
            )
            for observation in certain
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
        "Wrappers are compared through concrete leaves only when statically established.",
        "Absence in one source is not treated as a contradiction.",
        MAKE_DEFAULT_POLICY,
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
        comparable = {
            intent: items
            for intent, items in by_intent.items()
            if len({item.source for item in items}) >= 2
        }
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
        conflicts: list[str] = []
        for intent, items in comparable.items():
            concrete: dict[str, set[tuple[str, ...]]] = {}
            for item in items:
                if item.reason:
                    continue
                for leaf in item.resolved:
                    if leaf.family not in {"script", "container"}:
                        concrete.setdefault(item.source, set()).add(leaf.identity)
            sources = sorted(concrete)
            if any(
                concrete[left] != concrete[right]
                for index, left in enumerate(sources)
                for right in sources[index + 1 :]
            ):
                conflicts.append(intent)
        evidence = tuple(
            static_evidence(
                self.spec,
                "Recognized command source used for consistency comparison.",
                source=observation.source,
                excerpt=observation.excerpt,
                details=_observation_details(observation),
            )
            for observation in observations
        )
        if conflicts:
            return (
                make_finding(
                    self.spec,
                    title="Documented test/build commands are inconsistent",
                    message=(
                        "Independent sources use different concrete runners or targets "
                        f"for the same intent: {', '.join(conflicts)}."
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
        uncertain = [item for items in comparable.values() for item in items if item.reason]
        if uncertain:
            return (
                make_finding(
                    self.spec,
                    title="Command consistency is not verifiable",
                    message="A shared command intent has unresolved wrappers or shell syntax.",
                    assessment=Assessment.UNSUPPORTED,
                    state=ResultState.NOT_VERIFIED,
                    evidence=evidence,
                    applicability_reason=redact_excerpt(
                        "; ".join(dict.fromkeys(item.reason or "" for item in uncertain))
                    ),
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
