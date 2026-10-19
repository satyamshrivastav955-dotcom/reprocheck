"""Static package metadata, scripts, and declared entry consistency check."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import PurePosixPath

from ...models import (
    Assessment,
    CheckCategory,
    CheckSpec,
    Ecosystem,
    Finding,
    JSONValue,
    ResultState,
    Severity,
    VerificationClass,
)
from ..base import (
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

PACKAGE_SPEC = CheckSpec(
    check_id="RC-PKG-001",
    name="Package metadata/scripts/build entry consistency",
    category=CheckCategory.BUILD,
    verification_class=VerificationClass.STATIC,
    description=(
        "Check common Python and Node package metadata, script value types, and "
        "declared file entry points."
    ),
    severity=Severity.MEDIUM,
    supported_ecosystems=(Ecosystem.PYTHON, Ecosystem.NODE),
    limitations=(
        "Framework-specific metadata and complex export maps are not fully interpreted.",
        "Entry checks use discovered files and do not build the package.",
    ),
    false_positive_policy=(
        "Only declared concrete entry paths are checked; ambiguous export maps become "
        "not verified rather than passing."
    ),
    scoring_group="package-metadata",
)


def _safe_entry_path(value: str) -> bool:
    relative = PurePosixPath(value)
    return bool(
        value
        and not relative.is_absolute()
        and ".." not in relative.parts
        and relative.as_posix() == value
    )


def _entry_paths_from_package(parsed: dict[str, object]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("main", "module", "browser"):
        value = parsed.get(key)
        if isinstance(value, str):
            values.append(value)
    bins = parsed.get("bin")
    if isinstance(bins, str):
        values.append(bins)
    elif isinstance(bins, dict):
        values.extend(value for value in bins.values() if isinstance(value, str))
    return tuple(values)


def _validate_package(
    parsed: object, context: CheckContext
) -> tuple[bool, str | None, tuple[str, ...]]:
    if not isinstance(parsed, dict):
        return False, "package.json is not an object", ()
    name = parsed.get("name")
    if not isinstance(name, str) or not name.strip():
        return False, "package.json name is absent or empty", ()
    scripts = parsed.get("scripts", {})
    if not isinstance(scripts, dict):
        return False, "package.json scripts is not an object", ()
    if any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in scripts.items()
    ):
        return False, "package.json script names and values must be strings", ()
    entry_paths = _entry_paths_from_package(parsed)
    known = {repo_file.path for repo_file in context.discovery.files if not repo_file.is_symlink}
    for entry in entry_paths:
        if not _safe_entry_path(entry):
            return (
                False,
                f"declared package entry is not repository-relative: {entry!r}",
                entry_paths,
            )
        if entry not in known:
            return False, f"declared package entry was not discovered: {entry}", entry_paths
    exports = parsed.get("exports")
    if exports is not None and not isinstance(exports, (str, dict, list)):
        return False, "package exports has an unsupported shape", entry_paths
    return True, None, entry_paths


def _validate_python_project(parsed: object) -> tuple[bool, str | None]:
    if not isinstance(parsed, dict):
        return False, "pyproject.toml is not an object"
    project = parsed.get("project")
    if not isinstance(project, dict):
        return False, "pyproject.toml has no [project] table"
    if not isinstance(project.get("name"), str) or not str(project["name"]).strip():
        return False, "[project].name is absent or empty"
    scripts = project.get("scripts", {})
    if not isinstance(scripts, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in scripts.items()
    ):
        return False, "[project].scripts must map string names to string entry points"
    build_system = parsed.get("build-system")
    if build_system is not None:
        if not isinstance(build_system, dict):
            return False, "[build-system] is not a table"
        backend = build_system.get("build-backend")
        requires = build_system.get("requires")
        if backend is not None and (
            not isinstance(backend, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:\.[A-Za-z0-9_.-]+)*", backend)
        ):
            return False, "build-system.build-backend is not a module path"
        if requires is not None and (
            not isinstance(requires, list) or any(not isinstance(item, str) for item in requires)
        ):
            return False, "build-system.requires must be a list of strings"
    return True, None


class PackageConsistencyCheck(BaseCheck):
    spec = PACKAGE_SPEC

    def evaluate(self, context: CheckContext) -> tuple[Finding, ...]:
        names = {PurePosixPath(path).name.lower(): path for path in top_level_paths(context)}
        candidates: list[tuple[Ecosystem, str]] = []
        if "package.json" in names:
            candidates.append((Ecosystem.NODE, names["package.json"]))
        if "pyproject.toml" in names:
            candidates.append((Ecosystem.PYTHON, names["pyproject.toml"]))
        if not candidates:
            return (
                unsupported_finding(
                    self.spec,
                    title="Package metadata applicability is unknown",
                    message="No supported Python or Node package metadata file was discovered.",
                    reason="RC-PKG-001 is limited to package.json and pyproject.toml metadata.",
                ),
            )

        evidence = []
        for ecosystem, path in candidates:
            result = read_known_text(context, path)
            if result.status != "ok":
                if result.status == "inaccessible":
                    return (
                        blocked_finding(
                            self.spec,
                            title="Package metadata is inaccessible",
                            message=f"Package metadata {path} could not be read.",
                            reason=result.error or "package metadata is inaccessible",
                            path=path,
                        ),
                    )
                return (
                    parse_error_finding(
                        self.spec,
                        title="Package metadata cannot be assessed",
                        message=f"Package metadata {path} could not be read safely.",
                        reason=result.error or result.status,
                        path=path,
                    ),
                )
            try:
                if ecosystem == Ecosystem.NODE:
                    parsed: object = json.loads(result.text)
                    valid, error, entries = _validate_package(parsed, context)
                else:
                    parsed = tomllib.loads(result.text)
                    valid, error = _validate_python_project(parsed)
                    entries = ()
            except (json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
                return (
                    parse_error_finding(
                        self.spec,
                        title="Package metadata is malformed",
                        message=(
                            f"Package metadata {path} is not valid "
                            f"{('JSON' if ecosystem == Ecosystem.NODE else 'TOML')}."
                        ),
                        reason=str(error),
                        path=path,
                    ),
                )
            if not valid:
                return (
                    make_finding(
                        self.spec,
                        title="Package metadata is inconsistent",
                        message=(
                            f"Package metadata {path} has a missing, malformed, or "
                            "undiscovered declared entry."
                        ),
                        assessment=Assessment.FAIL,
                        state=ResultState.STATIC_FAIL,
                        evidence=(
                            static_evidence(
                                self.spec,
                                error or "package metadata failed consistency checks",
                                source=path,
                            ),
                        ),
                        path=path,
                        remediation=(
                            "Align package metadata, scripts, and declared entry files "
                            "with discovered repository content."
                        ),
                        code="package-metadata-inconsistent",
                        ecosystems=(ecosystem,),
                    ),
                )
            details: dict[str, JSONValue] = {"ecosystem": ecosystem.value}
            if entries:
                details["declared_entries"] = list(entries)
            evidence.append(
                static_evidence(
                    self.spec,
                    "Package metadata passed bounded consistency checks.",
                    source=path,
                    details=details,
                )
            )
        return (
            make_finding(
                self.spec,
                title="Package metadata and entries are consistent",
                message=(
                    "Supported package metadata, scripts, and declared entries pass "
                    "bounded static checks."
                ),
                assessment=Assessment.PASS,
                state=ResultState.LOOKS_CORRECT,
                evidence=tuple(evidence),
                ecosystems=tuple(ecosystem for ecosystem, _ in candidates),
            ),
        )


__all__ = ["PACKAGE_SPEC", "PackageConsistencyCheck"]
