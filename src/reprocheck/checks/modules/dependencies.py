"""Static dependency manifest and lockfile checks."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from ...models import (
    Assessment,
    CheckCategory,
    CheckSpec,
    Ecosystem,
    Finding,
    ResultState,
    Severity,
    VerificationClass,
)
from ..base import (
    ALL_RUNTIME_ECOSYSTEMS,
    BaseCheck,
    CheckContext,
    ReadStatus,
    blocked_finding,
    make_finding,
    parse_error_finding,
    read_known_text,
    static_evidence,
    top_level_paths,
    unsupported_finding,
)

MANIFEST_ECOSYSTEMS: dict[str, Ecosystem] = {
    "pyproject.toml": Ecosystem.PYTHON,
    "pipfile": Ecosystem.PYTHON,
    "setup.py": Ecosystem.PYTHON,
    "package.json": Ecosystem.NODE,
    "go.mod": Ecosystem.GO,
    "cargo.toml": Ecosystem.RUST,
    "pom.xml": Ecosystem.JAVA,
    "build.gradle": Ecosystem.JAVA,
    "build.gradle.kts": Ecosystem.JAVA,
    "*.csproj": Ecosystem.DOTNET,
    "*.fsproj": Ecosystem.DOTNET,
    "*.vbproj": Ecosystem.DOTNET,
    "gemfile": Ecosystem.RUBY,
    "composer.json": Ecosystem.PHP,
    "mix.exs": Ecosystem.ELIXIR,
}
LOCKFILE_NAMES: dict[Ecosystem, frozenset[str]] = {
    Ecosystem.PYTHON: frozenset({"uv.lock", "poetry.lock", "pipfile.lock", "pylock.toml"}),
    Ecosystem.NODE: frozenset({"package-lock.json", "pnpm-lock.yaml", "yarn.lock"}),
    Ecosystem.GO: frozenset({"go.sum"}),
    Ecosystem.RUST: frozenset({"cargo.lock"}),
    Ecosystem.JAVA: frozenset({"gradle.lockfile"}),
    Ecosystem.DOTNET: frozenset({"packages.lock.json"}),
    Ecosystem.RUBY: frozenset({"gemfile.lock"}),
    Ecosystem.PHP: frozenset({"composer.lock"}),
    Ecosystem.ELIXIR: frozenset({"mix.lock"}),
}


@dataclass(frozen=True)
class ManifestObservation:
    path: str
    ecosystem: Ecosystem
    valid: bool
    error: str | None = None
    status: ReadStatus = "ok"


def _manifest_ecosystem(name: str) -> Ecosystem | None:
    if name.startswith("requirements") and name.endswith(".txt"):
        return Ecosystem.PYTHON
    direct = MANIFEST_ECOSYSTEMS.get(name)
    if direct is not None:
        return direct
    if name.endswith((".csproj", ".fsproj", ".vbproj")):
        return Ecosystem.DOTNET
    return None


def manifest_paths(context: CheckContext) -> tuple[tuple[str, Ecosystem], ...]:
    """Return root-level recognized dependency manifest candidates."""

    found: list[tuple[str, Ecosystem]] = []
    for path in top_level_paths(context):
        ecosystem = _manifest_ecosystem(PurePosixPath(path).name.lower())
        if ecosystem is not None:
            found.append((path, ecosystem))
    return tuple(found)


def _validate_manifest(name: str, text: str) -> tuple[bool, str | None]:
    if name in {"pyproject.toml", "pipfile", "cargo.toml"}:
        try:
            parsed = tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            return False, f"invalid TOML: {error}"
        if not isinstance(parsed, dict):
            return False, "TOML document is not an object"
        return True, None
    if name in {"package.json", "composer.json"}:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            return False, f"invalid JSON at line {error.lineno}, column {error.colno}"
        if not isinstance(parsed, dict):
            return False, "JSON manifest is not an object"
        return True, None
    if name == "go.mod":
        return (
            (True, None)
            if re.search(r"(?m)^\s*module\s+\S+", text)
            else (False, "missing module declaration")
        )
    if name == "mix.exs":
        required = (re.search(r"\bdefmodule\s+", text), re.search(r"\buse\s+Mix\.Project\b", text))
        return (True, None) if all(required) else (False, "missing Mix project declaration")
    if name == "pom.xml" or name.endswith((".csproj", ".fsproj", ".vbproj")):
        return (
            (True, None)
            if re.search(r"<\s*(?:project|Project)\b", text)
            else (False, "missing XML project root")
        )
    if not text.strip():
        return False, "manifest is empty"
    return True, None


def manifest_observations(context: CheckContext) -> tuple[ManifestObservation, ...]:
    observations: list[ManifestObservation] = []
    for path, ecosystem in manifest_paths(context):
        result = read_known_text(context, path)
        if result.status == "ok":
            valid, error = _validate_manifest(PurePosixPath(path).name.lower(), result.text)
            observations.append(
                ManifestObservation(
                    path,
                    ecosystem,
                    valid,
                    error,
                    "ok" if valid else "malformed",
                )
            )
        else:
            observations.append(
                ManifestObservation(
                    path,
                    ecosystem,
                    False,
                    result.error or result.status,
                    result.status,
                )
            )
    return tuple(observations)


def active_manifest_ecosystems(context: CheckContext) -> tuple[Ecosystem, ...]:
    """Return deterministic supported ecosystems with a recognized manifest."""

    from_files = {ecosystem for _, ecosystem in manifest_paths(context)}
    return tuple(ecosystem for ecosystem in ALL_RUNTIME_ECOSYSTEMS if ecosystem in from_files)


def _validate_lockfile(context: CheckContext, path: str) -> tuple[str, str | None]:
    """Validate only lock formats that the standard library can parse safely."""

    result = read_known_text(context, path)
    if result.status != "ok":
        return result.status, result.error or result.status
    name = PurePosixPath(path).name.lower()
    if not result.text.strip():
        return "malformed", "lockfile is empty"
    if name.endswith(".json") or name == "pipfile.lock":
        try:
            parsed = json.loads(result.text)
        except json.JSONDecodeError as error:
            return "malformed", f"invalid JSON at line {error.lineno}, column {error.colno}"
        return (
            ("ok", None)
            if isinstance(parsed, dict)
            else ("malformed", "lockfile JSON is not an object")
        )
    if name == "pylock.toml" or (
        name.endswith((".lock", ".lockfile")) and name not in {"gemfile.lock", "mix.lock"}
    ):
        try:
            parsed = tomllib.loads(result.text)
        except tomllib.TOMLDecodeError as error:
            return "malformed", f"invalid TOML: {error}"
        return (
            ("ok", None)
            if isinstance(parsed, dict)
            else ("malformed", "lockfile TOML is not an object")
        )
    return "ok", None


def _tool_only_pyproject(context: CheckContext, observation: ManifestObservation) -> bool:
    if PurePosixPath(observation.path).name.lower() != "pyproject.toml":
        return False
    result = read_known_text(context, observation.path)
    if result.status != "ok":
        return False
    parsed = tomllib.loads(result.text)  # Syntax was checked by manifest_observations.
    tool = parsed.get("tool", {})
    return not any(key in parsed for key in ("project", "build-system", "dependency-groups")) and (
        not isinstance(tool, dict) or "poetry" not in tool
    )


_PINNED_REQUIREMENT_RE = re.compile(
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[A-Za-z0-9_, .-]+\])?"
    r"\s*={2,3}\s*[A-Za-z0-9][A-Za-z0-9.!+_-]*"
)


def _requirements_pins(
    context: CheckContext, paths: tuple[str, ...]
) -> tuple[Literal["pinned", "unlocked", "unsupported"], frozenset[str]]:
    """Recognize exact direct pins, without following includes or resolving a graph."""

    unsupported = not paths
    names: set[str] = set()
    for path in paths:
        result = read_known_text(context, path)
        if result.status != "ok":
            unsupported = True
            continue
        lines = re.sub(r"\\\r?\n\s*", " ", result.text).splitlines()
        found = False
        for raw_line in lines:
            line = re.split(r"\s+#", raw_line, maxsplit=1)[0].strip()
            if not line or line.startswith("#"):
                continue
            found = True
            if line.startswith("-") or "@" in line or "://" in line:
                unsupported = True
                continue
            requirement = line.split(";", 1)[0].strip()
            requirement = re.sub(r"\s+--hash=[A-Za-z0-9]+:[a-fA-F0-9]+", "", requirement)
            match = _PINNED_REQUIREMENT_RE.fullmatch(requirement)
            if match is None:
                return "unlocked", frozenset()
            names.add(re.sub(r"[-_.]+", "-", match["name"]).lower())
        unsupported = unsupported or not found
    return ("unsupported" if unsupported else "pinned"), frozenset(names)


def _pins_cover_python_manifests(
    context: CheckContext,
    observations: tuple[ManifestObservation, ...],
    requirements: tuple[str, ...],
    names: frozenset[str],
) -> bool:
    """Do not let an unrelated/dev-only pin list mask declared library dependencies."""

    for item in observations:
        if item.ecosystem != Ecosystem.PYTHON or item.path in requirements:
            continue
        if not any(PurePosixPath(path).name.lower() == "requirements.txt" for path in requirements):
            return False
        if PurePosixPath(item.path).name.lower() != "pyproject.toml":
            return False  # Legacy/executable metadata is not interpreted here.
        result = read_known_text(context, item.path)
        if result.status != "ok":
            return False
        project = tomllib.loads(result.text).get("project", {})
        if not isinstance(project, dict):
            return False
        dynamic = project.get("dynamic", [])
        if not isinstance(dynamic, list) or "dependencies" in dynamic:
            return False
        dependencies = project.get("dependencies", [])
        if not isinstance(dependencies, list):
            return False
        for dependency in dependencies:
            match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)", str(dependency))
            if match is None or re.sub(r"[-_.]+", "-", match[1]).lower() not in names:
                return False
    return True


MANIFEST_SPEC = CheckSpec(
    check_id="RC-DEP-001",
    name="Recognized dependency manifest",
    category=CheckCategory.DEPENDENCIES,
    verification_class=VerificationClass.STATIC,
    description=(
        "Detect a recognized root-level dependency manifest and check only its bounded syntax."
    ),
    severity=Severity.INFO,
    supported_ecosystems=ALL_RUNTIME_ECOSYSTEMS,
    limitations=(
        "Only common root-level manifests are recognized.",
        "Dependency resolution is not performed.",
    ),
    false_positive_policy=(
        "Report presence separately from syntax; do not infer installability from a manifest."
    ),
    scoring_group="dependencies",
)


class RecognizedDependencyManifestCheck(BaseCheck):
    spec = MANIFEST_SPEC

    def evaluate(self, context: CheckContext) -> tuple[Finding, ...]:
        observations = manifest_observations(context)
        if any(not observation.valid for observation in observations):
            observation = next(item for item in observations if not item.valid)
            if observation.status == "inaccessible":
                return (
                    blocked_finding(
                        self.spec,
                        title="Dependency manifest is inaccessible",
                        message=(
                            f"The recognized dependency manifest {observation.path} "
                            "could not be read."
                        ),
                        reason=observation.error or "manifest is inaccessible",
                        path=observation.path,
                    ),
                )
            return (
                parse_error_finding(
                    self.spec,
                    title="Dependency manifest is not statically valid",
                    message=(
                        f"The recognized dependency manifest {observation.path} could not "
                        "be validated."
                    ),
                    reason=observation.error or "manifest syntax is unknown",
                    path=observation.path,
                ),
            )
        if not observations:
            supported = tuple(
                ecosystem
                for ecosystem in ALL_RUNTIME_ECOSYSTEMS
                if ecosystem in context.discovery.ecosystems
            )
            if not supported:
                return (
                    unsupported_finding(
                        self.spec,
                        title="Dependency manifest applicability is unknown",
                        message="No supported dependency ecosystem was discovered.",
                        reason="No recognized supported ecosystem marker was discovered.",
                    ),
                )
            evidence = static_evidence(
                self.spec, "No recognized dependency manifest was discovered."
            )
            return (
                make_finding(
                    self.spec,
                    title="Dependency manifest is missing",
                    message=(
                        "A supported ecosystem was discovered without a recognized "
                        "dependency manifest."
                    ),
                    assessment=Assessment.FAIL,
                    state=ResultState.STATIC_FAIL,
                    evidence=(evidence,),
                    remediation=(
                        "Add the ecosystem's standard dependency manifest at the repository root."
                    ),
                    ecosystems=supported,
                ),
            )
        evidence_items = tuple(
            static_evidence(
                self.spec, "Recognized dependency manifest detected.", source=observation.path
            )
            for observation in observations
        )
        ecosystems = tuple(dict.fromkeys(observation.ecosystem for observation in observations))
        return (
            make_finding(
                self.spec,
                title="Dependency manifest detected",
                message="At least one recognized dependency manifest is present.",
                assessment=Assessment.INFO,
                state=ResultState.PRESENT,
                evidence=evidence_items,
                ecosystems=ecosystems,
            ),
        )


LOCK_SPEC = CheckSpec(
    check_id="RC-DEP-002",
    name="Dependency lockfile presence",
    category=CheckCategory.DEPENDENCIES,
    verification_class=VerificationClass.STATIC,
    description=(
        "Check for a recognized lockfile corresponding to each detected dependency "
        "manifest ecosystem."
    ),
    severity=Severity.HIGH,
    supported_ecosystems=ALL_RUNTIME_ECOSYSTEMS,
    limitations=(
        "Presence is checked without resolving or comparing dependency graphs.",
        "Exact requirements pins cover declared entries only, not transitive dependency closure.",
        "Requirements includes, editable installs, and direct URLs are not resolved.",
    ),
    false_positive_policy="Do not treat an unfamiliar lockfile as satisfying the check.",
    scoring_group="dependencies",
)


class LockfilePresenceCheck(BaseCheck):
    spec = LOCK_SPEC

    def evaluate(self, context: CheckContext) -> tuple[Finding, ...]:
        observations = manifest_observations(context)
        if any(not observation.valid for observation in observations):
            observation = next(item for item in observations if not item.valid)
            if observation.status == "inaccessible":
                return (
                    blocked_finding(
                        self.spec,
                        title="Lockfile applicability is blocked",
                        message=f"The dependency manifest {observation.path} could not be read.",
                        reason=observation.error or "manifest is inaccessible",
                        path=observation.path,
                    ),
                )
            return (
                parse_error_finding(
                    self.spec,
                    title="Lockfile applicability cannot be assessed",
                    message=f"The dependency manifest {observation.path} is malformed.",
                    reason=observation.error or "manifest syntax is unknown",
                    path=observation.path,
                ),
            )
        observations = tuple(
            item for item in observations if not _tool_only_pyproject(context, item)
        )
        ecosystems = tuple(dict.fromkeys(observation.ecosystem for observation in observations))
        if not ecosystems:
            return (
                unsupported_finding(
                    self.spec,
                    title="Lockfile applicability is unknown",
                    message="No recognized dependency declaration was discovered.",
                    reason=(
                        "Lockfile expectations require dependency declarations; "
                        "tool-only configuration does not establish applicability."
                    ),
                ),
            )

        paths = {PurePosixPath(path).name.lower(): path for path in top_level_paths(context)}
        missing = [
            ecosystem
            for ecosystem in ecosystems
            if not any(name in paths for name in LOCKFILE_NAMES.get(ecosystem, frozenset()))
        ]
        present = tuple(
            paths[name]
            for ecosystem in ecosystems
            for name in sorted(LOCKFILE_NAMES.get(ecosystem, frozenset()))
            if name in paths
        )
        for lockfile_path in present:
            status, error = _validate_lockfile(context, lockfile_path)
            if status == "inaccessible":
                return (
                    blocked_finding(
                        self.spec,
                        title="Dependency lockfile is inaccessible",
                        message=f"The recognized lockfile {lockfile_path} could not be read.",
                        reason=error or "lockfile is inaccessible",
                        path=lockfile_path,
                    ),
                )
            if status != "ok":
                return (
                    parse_error_finding(
                        self.spec,
                        title="Dependency lockfile is malformed",
                        message=(
                            f"The recognized lockfile {lockfile_path} failed bounded syntax checks."
                        ),
                        reason=error or "lockfile is malformed",
                        path=lockfile_path,
                    ),
                )
        pinned_paths: tuple[str, ...] = ()
        requirements: tuple[str, ...] = ()
        uncertain_python = False
        if Ecosystem.PYTHON in missing:
            requirements = tuple(
                item.path
                for item in observations
                if PurePosixPath(item.path).name.lower().startswith("requirements")
                and PurePosixPath(item.path).suffix.lower() == ".txt"
            )
            pin_status, pinned_names = _requirements_pins(context, requirements)
            if pin_status == "pinned" and _pins_cover_python_manifests(
                context, observations, requirements, pinned_names
            ):
                pinned_paths = requirements
                missing.remove(Ecosystem.PYTHON)
            elif requirements and pin_status == "unsupported":
                # A separate project/Pipfile still establishes an applicable missing lock.
                uncertain_python = all(
                    item.path in requirements
                    for item in observations
                    if item.ecosystem == Ecosystem.PYTHON
                )
                if uncertain_python:
                    missing.remove(Ecosystem.PYTHON)
        uncertainty_evidence = tuple(
            static_evidence(
                self.spec,
                "Requirements pinning is unsupported by the bounded exact-pin check.",
                source=path,
                details={"ecosystem": "python", "evidence_kind": "unsupported"},
            )
            for path in requirements
            if uncertain_python
        )
        if missing:
            evidence = tuple(
                static_evidence(
                    self.spec,
                    "No recognized lockfile was discovered for this ecosystem.",
                    details={"ecosystem": ecosystem.value},
                )
                for ecosystem in missing
            )
            return (
                make_finding(
                    self.spec,
                    title="Dependency lockfile is missing",
                    message="One or more recognized dependency ecosystems have no lockfile.",
                    assessment=Assessment.FAIL,
                    state=ResultState.STATIC_FAIL,
                    evidence=evidence + uncertainty_evidence,
                    remediation=(
                        "Commit the lockfile produced by the ecosystem's supported "
                        "dependency manager."
                    ),
                    ecosystems=ecosystems,
                ),
            )
        evidence_items = tuple(
            static_evidence(self.spec, "Recognized lockfile detected.", source=path)
            for path in present
        ) + tuple(
            static_evidence(
                self.spec,
                "Exact requirements pins detected; transitive closure is not verified.",
                source=path,
                details={"ecosystem": "python", "evidence_kind": "exact-requirements-pins"},
            )
            for path in pinned_paths
        )
        if uncertain_python:
            return (
                make_finding(
                    self.spec,
                    title="Python dependency pinning cannot be assessed",
                    message="Requirements use declarations outside the bounded exact-pin check.",
                    assessment=Assessment.UNKNOWN if present else Assessment.UNSUPPORTED,
                    state=ResultState.NOT_VERIFIED,
                    evidence=evidence_items + uncertainty_evidence,
                    ecosystems=ecosystems,
                    applicability_reason=(
                        "Requirements includes, URLs, or empty files do not establish pinning."
                    ),
                ),
            )
        return (
            make_finding(
                self.spec,
                title="Dependency lockfile or exact pins detected",
                message=(
                    "Each applicable dependency ecosystem has a recognized lockfile or "
                    "exact requirements pins; dependency graph completeness is not verified."
                ),
                assessment=Assessment.INFO,
                state=ResultState.PRESENT,
                evidence=evidence_items,
                ecosystems=ecosystems,
            ),
        )


__all__ = [
    "LOCK_SPEC",
    "MANIFEST_SPEC",
    "LockfilePresenceCheck",
    "ManifestObservation",
    "RecognizedDependencyManifestCheck",
    "active_manifest_ecosystems",
    "manifest_observations",
    "manifest_paths",
]
