"""Static runtime declaration and pin checks."""

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

RUNTIME_SPEC = CheckSpec(
    check_id="RC-RUN-001",
    name="Runtime declaration and pin",
    category=CheckCategory.RUNTIME,
    verification_class=VerificationClass.STATIC,
    description="Find a bounded Python or Node runtime declaration or version pin.",
    severity=Severity.MEDIUM,
    supported_ecosystems=(Ecosystem.PYTHON, Ecosystem.NODE),
    limitations=("Version ranges are not resolved.", "Only root-level declarations are checked."),
    false_positive_policy=(
        "A syntactically plausible declaration is not proof that the runtime is "
        "installed or compatible."
    ),
    scoring_group="runtime",
)


def _root_file_names(context: CheckContext) -> dict[str, str]:
    return {PurePosixPath(path).name.lower(): path for path in top_level_paths(context)}


def _python_runtime_candidates(context: CheckContext) -> tuple[str, ...]:
    names = _root_file_names(context)
    candidates = [names[name] for name in (".python-version", "pyproject.toml") if name in names]
    return tuple(candidates)


def _node_runtime_candidates(context: CheckContext) -> tuple[str, ...]:
    names = _root_file_names(context)
    return tuple(names[name] for name in (".nvmrc", "package.json") if name in names)


def _validate_python_runtime(path: str, text: str) -> tuple[bool, str | None]:
    name = PurePosixPath(path).name.lower()
    if name == ".python-version":
        version = text.strip().splitlines()[0] if text.strip() else ""
        if re.fullmatch(r"\d+(?:\.\d+){1,2}(?:[-+][A-Za-z0-9._-]+)?", version):
            return True, None
        return False, "expected a Python version such as 3.12 or 3.12.4"
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        return False, f"invalid TOML: {error}"
    project = parsed.get("project") if isinstance(parsed, dict) else None
    if not isinstance(project, dict):
        return False, "pyproject.toml has no [project] table"
    value = project.get("requires-python")
    if not isinstance(value, str) or not value.strip():
        return False, "[project].requires-python is absent or empty"
    return True, None


def _validate_node_runtime(path: str, text: str) -> tuple[bool, str | None]:
    name = PurePosixPath(path).name.lower()
    if name == ".nvmrc":
        version = text.strip().splitlines()[0] if text.strip() else ""
        if re.fullmatch(r"(?:v?\d+(?:\.\d+){0,2}|lts/[A-Za-z0-9._-]+)", version):
            return True, None
        return False, "expected a Node version such as 20, 20.11, or lts/*"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        return False, f"invalid JSON at line {error.lineno}, column {error.colno}"
    if not isinstance(parsed, dict):
        return False, "package.json is not an object"
    engines = parsed.get("engines")
    if (
        not isinstance(engines, dict)
        or not isinstance(engines.get("node"), str)
        or not engines["node"].strip()
    ):
        return False, "package.json has no non-empty engines.node declaration"
    return True, None


class RuntimeDeclarationCheck(BaseCheck):
    spec = RUNTIME_SPEC

    def evaluate(self, context: CheckContext) -> tuple[Finding, ...]:
        active = tuple(
            ecosystem
            for ecosystem in (Ecosystem.PYTHON, Ecosystem.NODE)
            if ecosystem in context.discovery.ecosystems
        )
        if not active:
            return (
                unsupported_finding(
                    self.spec,
                    title="Runtime declaration is not applicable",
                    message="No supported Python or Node ecosystem was discovered.",
                    reason="RC-RUN-001 supports Python and Node repositories.",
                ),
            )

        candidates: list[tuple[Ecosystem, str]] = []
        for ecosystem in active:
            paths = (
                _python_runtime_candidates(context)
                if ecosystem == Ecosystem.PYTHON
                else _node_runtime_candidates(context)
            )
            if paths:
                candidates.append((ecosystem, paths[0]))
        if not candidates:
            evidence_item = static_evidence(
                self.spec,
                "No supported runtime declaration or pin was discovered.",
            )
            return (
                make_finding(
                    self.spec,
                    title="Runtime declaration is missing",
                    message=(
                        "A supported runtime ecosystem was discovered without a "
                        "root-level runtime declaration or pin."
                    ),
                    assessment=Assessment.FAIL,
                    state=ResultState.STATIC_FAIL,
                    evidence=(evidence_item,),
                    remediation=(
                        "Commit a runtime pin or a bounded runtime declaration for the "
                        "detected ecosystem."
                    ),
                    ecosystems=active,
                ),
            )

        valid: list[tuple[Ecosystem, str]] = []
        for ecosystem, path in candidates:
            result = read_known_text(context, path)
            if result.status != "ok":
                if result.status == "inaccessible":
                    return (
                        blocked_finding(
                            self.spec,
                            title="Runtime declaration is inaccessible",
                            message=f"The runtime declaration {path} could not be read.",
                            reason=result.error or "runtime declaration is inaccessible",
                            path=path,
                        ),
                    )
                return (
                    parse_error_finding(
                        self.spec,
                        title="Runtime declaration cannot be assessed",
                        message=f"The runtime declaration {path} could not be read as valid text.",
                        reason=result.error or result.status,
                        path=path,
                    ),
                )
            validator = (
                _validate_python_runtime
                if ecosystem == Ecosystem.PYTHON
                else _validate_node_runtime
            )
            is_valid, error = validator(path, result.text)
            if not is_valid:
                return (
                    parse_error_finding(
                        self.spec,
                        title="Runtime declaration is malformed",
                        message=(
                            f"The runtime declaration {path} is not a bounded static "
                            "runtime declaration."
                        ),
                        reason=error or "runtime declaration is malformed",
                        path=path,
                    ),
                )
            valid.append((ecosystem, path))

        evidence_items = tuple(
            static_evidence(
                self.spec,
                "Runtime declaration or pin passed bounded static checks.",
                source=path,
            )
            for _, path in valid
        )
        return (
            make_finding(
                self.spec,
                title="Runtime declaration found",
                message=(
                    "A supported runtime declaration or pin is present and syntactically bounded."
                ),
                assessment=Assessment.PASS,
                state=ResultState.LOOKS_CORRECT,
                evidence=evidence_items,
                ecosystems=active,
            ),
        )


__all__ = ["RUNTIME_SPEC", "RuntimeDeclarationCheck"]
