"""Allowlisted verification profiles and explicit command plans."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Final

from ..discovery import discover_repository, resolve_repository_root
from ..models import CommandSpec, Ecosystem


class ProfileError(ValueError):
    """Raised when a repository cannot satisfy a recognized safe profile."""


class PlanPhase(str, Enum):
    ACQUISITION = "acquisition"
    VERIFICATION = "verification"


@dataclass(frozen=True)
class AcquisitionPolicy:
    """Network requirement for a plan's dependency acquisition phase."""

    phase: PlanPhase = PlanPhase.ACQUISITION
    network_required: bool = True
    reason: str = "Dependency acquisition may contact package registries."


@dataclass(frozen=True)
class PlannedCommand:
    """One argv command with its phase and network requirement."""

    command: CommandSpec
    phase: PlanPhase
    network_required: bool
    purpose: str

    @property
    def argv(self) -> tuple[str, ...]:
        """Return the exact argv array that an executor may pass to Popen."""

        return (self.command.executable, *self.command.args)


@dataclass(frozen=True)
class VerificationPlan:
    """A complete, still-unapproved verification plan."""

    repository: Path
    profile: str
    ecosystem: Ecosystem
    commands: tuple[PlannedCommand, ...]
    acquisition: AcquisitionPolicy
    limitations: tuple[str, ...] = ()

    @property
    def acquisition_network_required(self) -> bool:
        return any(
            item.phase is PlanPhase.ACQUISITION and item.network_required for item in self.commands
        )

    @property
    def verification_network_required(self) -> bool:
        return any(
            item.phase is PlanPhase.VERIFICATION and item.network_required for item in self.commands
        )


PYTHON_UV_PROFILE: Final[str] = "python-uv"
PYTHON_PIP_PROFILE: Final[str] = "python-pip-editable"
NODE_NPM_PROFILE: Final[str] = "node-npm"
_PROFILE_ALIASES: Final[dict[str, str]] = {
    "python": PYTHON_UV_PROFILE,
    "python-uv": PYTHON_UV_PROFILE,
    "python-pip": PYTHON_PIP_PROFILE,
    "python-pip-editable": PYTHON_PIP_PROFILE,
    "node": NODE_NPM_PROFILE,
    "node-npm": NODE_NPM_PROFILE,
}


def available_profiles() -> tuple[str, ...]:
    """Return profiles whose command vocabulary is explicitly supported."""

    return (PYTHON_UV_PROFILE, PYTHON_PIP_PROFILE, NODE_NPM_PROFILE)


def _regular_file(root: Path, relative: str) -> Path | None:
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=True))
    except (OSError, RuntimeError, ValueError):
        raise ProfileError(f"repository path escapes root: {relative!r}") from None
    if candidate.is_symlink() or not candidate.is_file():
        return None
    return candidate


def _read_package_json(root: Path) -> dict[str, object]:
    package_path = _regular_file(root, "package.json")
    if package_path is None:
        raise ProfileError("node-npm requires a regular package.json")
    try:
        if package_path.stat().st_size > 1024 * 1024:
            raise ProfileError("package.json exceeds the bounded profile read size")
        value = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProfileError(f"cannot read package.json safely: {error}") from error
    if not isinstance(value, dict):
        raise ProfileError("package.json must contain a JSON object")
    return value


def _command(
    executable: str,
    args: tuple[str, ...],
    *,
    phase: PlanPhase,
    network_required: bool,
    purpose: str,
    timeout_seconds: float | None,
) -> PlannedCommand:
    return PlannedCommand(
        command=CommandSpec(
            executable=executable,
            args=args,
            cwd=".",
            timeout_seconds=timeout_seconds,
            description=purpose,
        ),
        phase=phase,
        network_required=network_required,
        purpose=purpose,
    )


def _python_plan(
    root: Path,
    profile: str,
    *,
    include_tests: bool,
    timeout: float | None,
) -> VerificationPlan:
    summary = discover_repository(root)
    names = {PurePosixPath(item.path).name.lower() for item in summary.files if not item.is_symlink}
    has_project = "pyproject.toml" in names or "setup.py" in names
    if not has_project:
        raise ProfileError("a Python profile requires pyproject.toml or setup.py")

    limitations: list[str] = []
    if profile == PYTHON_UV_PROFILE:
        if "pyproject.toml" not in names or "uv.lock" not in names:
            raise ProfileError("python-uv requires both pyproject.toml and uv.lock")
        install = _command(
            "uv",
            ("sync", "--locked"),
            phase=PlanPhase.ACQUISITION,
            network_required=True,
            purpose="Synchronize the locked Python environment",
            timeout_seconds=timeout,
        )
        test_executable = "uv"
        test_args: tuple[str, ...] = ("run", "python", "-m", "pytest")
    else:
        install = _command(
            "python",
            ("-m", "pip", "install", "-e", "."),
            phase=PlanPhase.ACQUISITION,
            network_required=True,
            purpose="Install the Python project in editable mode (explicit profile)",
            timeout_seconds=timeout,
        )
        test_executable = "python"
        test_args = ("-m", "pytest")
        limitations.append(
            "The editable pip profile was explicitly selected; it is not an implicit fallback."
        )

    commands = [install]
    has_tests = any(
        item.path == "tests" or item.path.startswith("tests/") for item in summary.files
    )
    if include_tests and has_tests:
        commands.append(
            _command(
                test_executable,
                test_args,
                phase=PlanPhase.VERIFICATION,
                network_required=False,
                purpose="Run the Python test suite",
                timeout_seconds=timeout,
            )
        )
    elif include_tests:
        limitations.append(
            "No repository tests directory was discovered; no Python test command was planned."
        )

    return VerificationPlan(
        repository=root,
        profile=profile,
        ecosystem=Ecosystem.PYTHON,
        commands=tuple(commands),
        acquisition=AcquisitionPolicy(),
        limitations=tuple(limitations),
    )


def _node_plan(
    root: Path,
    *,
    include_build: bool,
    include_test: bool,
    timeout: float | None,
) -> VerificationPlan:
    summary = discover_repository(root)
    names = {PurePosixPath(item.path).name.lower() for item in summary.files if not item.is_symlink}
    if "package-lock.json" not in names:
        raise ProfileError(
            "node-npm requires package-lock.json; npm install is not an approved profile command"
        )
    package = _read_package_json(root)
    scripts_value = package.get("scripts", {})
    scripts = scripts_value if isinstance(scripts_value, dict) else {}
    commands = [
        _command(
            "npm",
            ("ci",),
            phase=PlanPhase.ACQUISITION,
            network_required=True,
            purpose="Install the locked Node dependency tree",
            timeout_seconds=timeout,
        )
    ]
    limitations: list[str] = []
    if include_test and isinstance(scripts.get("test"), str):
        commands.append(
            _command(
                "npm",
                ("test",),
                phase=PlanPhase.VERIFICATION,
                network_required=False,
                purpose="Run the package.json test script",
                timeout_seconds=timeout,
            )
        )
    elif include_test:
        limitations.append("package.json has no test script; no npm test command was planned.")
    if include_build and isinstance(scripts.get("build"), str):
        commands.append(
            _command(
                "npm",
                ("run", "build"),
                phase=PlanPhase.VERIFICATION,
                network_required=False,
                purpose="Run the package.json build script",
                timeout_seconds=timeout,
            )
        )
    elif include_build:
        limitations.append("package.json has no build script; no npm build command was planned.")

    return VerificationPlan(
        repository=root,
        profile=NODE_NPM_PROFILE,
        ecosystem=Ecosystem.NODE,
        commands=tuple(commands),
        acquisition=AcquisitionPolicy(),
        limitations=tuple(limitations),
    )


def plan_verification(
    repository: Path,
    *,
    profile: str | None = None,
    include_tests: bool = True,
    include_test: bool = True,
    include_build: bool = True,
    timeout_seconds: float | None = 600.0,
) -> VerificationPlan:
    """Create a recognized plan without executing any repository command.

    The default Python choice is uv only when a lockfile is present.  The
    editable pip profile can be selected by name, but is never selected as an
    implicit fallback.  Node planning accepts only ``npm ci`` and scripts
    exposed by ``package.json``.
    """

    root = resolve_repository_root(repository)
    summary = discover_repository(root)
    detected = set(summary.ecosystems)
    selected = _PROFILE_ALIASES.get(profile.lower()) if profile is not None else None
    if profile is not None and selected is None:
        raise ProfileError(f"unknown verification profile: {profile!r}")

    if selected is None:
        names = {
            PurePosixPath(item.path).name.lower() for item in summary.files if not item.is_symlink
        }
        if Ecosystem.PYTHON in detected and "uv.lock" in names and "pyproject.toml" in names:
            selected = PYTHON_UV_PROFILE
        elif Ecosystem.NODE in detected and "package-lock.json" in names:
            selected = NODE_NPM_PROFILE
        else:
            raise ProfileError(
                "no recognized safe profile; select python-pip-editable explicitly for editable pip"
            )

    if selected in {PYTHON_UV_PROFILE, PYTHON_PIP_PROFILE}:
        return _python_plan(
            root,
            selected,
            include_tests=include_tests,
            timeout=timeout_seconds,
        )
    return _node_plan(
        root,
        include_build=include_build,
        include_test=include_test,
        timeout=timeout_seconds,
    )


def create_verification_plan(
    repository: Path,
    **kwargs: object,
) -> VerificationPlan:
    """Compatibility name for :func:`plan_verification`."""

    return plan_verification(repository, **kwargs)  # type: ignore[arg-type]


build_plan = plan_verification


__all__ = [
    "AcquisitionPolicy",
    "NODE_NPM_PROFILE",
    "PYTHON_PIP_PROFILE",
    "PYTHON_UV_PROFILE",
    "PlanPhase",
    "PlannedCommand",
    "ProfileError",
    "VerificationPlan",
    "available_profiles",
    "build_plan",
    "create_verification_plan",
    "plan_verification",
]
