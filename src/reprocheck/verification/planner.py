"""Allowlisted verification profiles and explicit command plans."""

from __future__ import annotations

import configparser
import json
import re
import tomllib
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


_PYTHON_READ_LIMIT: Final[int] = 1024 * 1024
_TEST_DEPENDENCY_NAMES: Final[frozenset[str]] = frozenset(
    {"dev", "development", "test", "tests", "testing"}
)
_REQUIREMENT: Final[re.Pattern[str]] = re.compile(
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[[A-Za-z0-9_., -]+\])?\s*"
    r"(?:(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9*+!._-]+"
    r"(?:\s*,\s*(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9*+!._-]+)*)?"
    r"\s*(?:;[^\r\n\x00]+)?"
)


def _python_relative_path(relative: str) -> PurePosixPath:
    path = PurePosixPath(relative)
    if (
        path.is_absolute()
        or not path.parts
        or ".." in path.parts
        or any(character in relative for character in ("\\", ":", "$", "\x00"))
    ):
        raise ProfileError(f"Python profile path must stay inside the repository: {relative!r}")
    return path


def _python_file(root: Path, relative: str) -> Path | None:
    """Accept only local regular files, including every parent component."""

    path = _python_relative_path(relative)
    candidate = root
    try:
        for part in path.parts:
            candidate /= part
            if candidate.is_symlink():
                raise ProfileError(f"Python profile paths must not contain symlinks: {relative!r}")
        result = _regular_file(root, relative)
        if result is None and candidate.exists():
            raise ProfileError(f"Python profile requires a regular file: {relative!r}")
        return result
    except OSError as error:
        raise ProfileError(f"cannot inspect Python profile path {relative!r}: {error}") from error


def _python_text(root: Path, relative: str) -> str | None:
    path = _python_file(root, relative)
    if path is None:
        return None
    try:
        with path.open("rb") as stream:
            content = stream.read(_PYTHON_READ_LIMIT + 1)
        if len(content) > _PYTHON_READ_LIMIT:
            raise ProfileError(f"{relative} exceeds the bounded Python profile read size")
        return content.decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise ProfileError(f"cannot read {relative} safely: {error}") from error


def _table(value: object, source: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProfileError(f"{source} must be a table")
    return value


def _python_metadata(root: Path) -> dict[str, object]:
    text = _python_text(root, "pyproject.toml")
    if text is None:
        return {}
    try:
        return tomllib.loads(text)
    except (ValueError, RecursionError) as error:
        raise ProfileError(f"cannot parse pyproject.toml safely: {error}") from error


class _PythonConfig(configparser.ConfigParser):
    def optionxform(self, optionstr: str) -> str:
        """Preserve declared extra names for pip."""

        return optionstr


def _python_ini(root: Path, relative: str) -> configparser.ConfigParser:
    config = _PythonConfig(interpolation=None)
    text = _python_text(root, relative)
    if text is not None:
        try:
            config.read_string(text)
        except configparser.Error as error:
            raise ProfileError(f"cannot parse {relative} safely: {error}") from error
    return config


def _requirement_name(requirement: str, source: str) -> str | None:
    """Recognize registry requirements; conditional deps cannot prove runner availability."""

    match = _REQUIREMENT.fullmatch(requirement.strip())
    if match is None:
        raise ProfileError(
            f"unsupported dependency in {source}; use explicit registry requirements, "
            "not local paths, URLs, installer options, or dynamic metadata"
        )
    return re.sub(r"[-_.]+", "-", match["name"]).lower() if ";" not in requirement else None


def _dependency_names(value: object, source: str, installed: set[str]) -> set[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ProfileError(f"{source} must be a list of dependency strings")
    names: set[str] = set()
    for item in value:
        name = _requirement_name(item, source)
        if name is not None:
            names.add(name)
            installed.add(item.strip())
    return names


def _requirements_names(root: Path, relative: str, installed: set[str]) -> set[str]:
    """Inspect bounded local -r/-c trees, keeping constraints out of installed deps."""

    reads = 0

    def visit(filename: str, stack: tuple[str, ...], *, constraint: bool) -> set[str]:
        nonlocal reads
        reads += 1
        if filename in stack or len(stack) >= 16 or reads > 32:
            raise ProfileError("requirements includes must be acyclic and within depth/file bounds")
        text = _python_text(root, filename)
        if text is None:
            raise ProfileError(
                f"requirements include is not a regular repository file: {filename!r}"
            )
        names: set[str] = set()
        for raw_line in text.splitlines():
            line = re.split(r"\s+#", raw_line, maxsplit=1)[0].strip()
            if not line or line.startswith("#"):
                continue
            include = re.fullmatch(
                r"(?:(-r|-c)\s*|(--requirement|--constraint)(?:\s+|=))(.+)", line
            )
            if include is not None:
                option = include[1] or include[2]
                target = include[3].strip()
                # Validate before joining: a URL or absolute path must not be relativized.
                child = (PurePosixPath(filename).parent / _python_relative_path(target)).as_posix()
                names.update(
                    visit(
                        child,
                        (*stack, filename),
                        constraint=constraint or option in {"-c", "--constraint"},
                    )
                )
            else:
                name = _requirement_name(line, filename)
                if name is not None and not constraint:
                    names.add(name)
                    installed.add(line)
        return names

    return visit(relative, (), constraint=False)


@dataclass(frozen=True)
class _PipDependencies:
    install_args: tuple[str, ...]
    names: frozenset[str]
    requirements: frozenset[str]
    requirement_files: frozenset[str]


def _pip_test_dependencies(
    root: Path,
    metadata: dict[str, object],
    setup: configparser.ConfigParser,
) -> _PipDependencies:
    project = _table(metadata.get("project", {}), "project")
    groups = _table(metadata.get("dependency-groups", {}), "dependency-groups")
    if any(name.lower() in _TEST_DEPENDENCY_NAMES for name in groups):
        raise ProfileError(
            "python-pip-editable cannot install PEP 735 dependency-groups as extras; "
            "declare test/dev dependencies in requirements-test.txt/requirements-dev.txt "
            "or project.optional-dependencies, or select python-uv with a real uv.lock"
        )
    dynamic = project.get("dynamic", [])
    if not isinstance(dynamic, list) or any(not isinstance(item, str) for item in dynamic):
        raise ProfileError("project.dynamic must be a list of strings")
    if {"dependencies", "optional-dependencies"}.intersection(dynamic):
        raise ProfileError(
            "dynamic test dependencies require explicit static requirements metadata"
        )
    if "project" in metadata and (
        setup.has_option("options", "install_requires")
        or setup.has_section("options.extras_require")
    ):
        raise ProfileError(
            "ambiguous pyproject.toml and setup.cfg dependency metadata; "
            "declare dependencies and test/dev extras in one static metadata source"
        )

    installed: set[str] = set()
    names = _dependency_names(project.get("dependencies", []), "project.dependencies", installed)
    extras: set[str] = set()
    optional = _table(project.get("optional-dependencies", {}), "project.optional-dependencies")
    for extra, dependencies in optional.items():
        if extra.lower() in _TEST_DEPENDENCY_NAMES:
            extras.add(extra)
            names.update(
                _dependency_names(dependencies, f"project.optional-dependencies.{extra}", installed)
            )
    if setup.has_option("options", "install_requires"):
        requirements = setup.get("options", "install_requires").splitlines()
        names.update(
            _dependency_names(
                [
                    line.strip()
                    for line in requirements
                    if line.strip() and not line.lstrip().startswith("#")
                ],
                "setup.cfg install_requires",
                installed,
            )
        )
    if setup.has_section("options.extras_require"):
        for extra, text in setup.items("options.extras_require"):
            if extra.lower() in _TEST_DEPENDENCY_NAMES:
                extras.add(extra)
                names.update(
                    _dependency_names(
                        [
                            line.strip()
                            for line in text.splitlines()
                            if line.strip() and not line.lstrip().startswith("#")
                        ],
                        f"setup.cfg extra {extra}",
                        installed,
                    )
                )

    target = f".[{','.join(sorted(extras))}]" if extras else "."
    args = ["-m", "pip", "install", "-e", target]
    requirement_files: set[str] = set()
    for filename in ("requirements-dev.txt", "requirements-test.txt"):
        if _python_file(root, filename) is not None:
            names.update(_requirements_names(root, filename, installed))
            requirement_files.add(filename)
            args.extend(("-r", filename))
    return _PipDependencies(
        tuple(args), frozenset(names), frozenset(installed), frozenset(requirement_files)
    )


def _check_tox_dependencies(
    tox: configparser.ConfigParser,
    dependencies: _PipDependencies,
) -> None:
    """Accept literal duplicates only; do not add dependencies or interpret tox substitutions."""

    unsupported = any(
        tox.has_option("testenv", option) and tox.get("testenv", option).strip()
        for option in ("extras", "dependency_groups")
    )
    if tox.has_option("testenv", "deps"):
        for raw_line in tox.get("testenv", "deps").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            include = re.fullmatch(r"(?:-r\s*|--requirement(?:\s+|=))(.+)", line)
            if include is not None:
                filename = _python_relative_path(include[1].strip()).as_posix()
                unsupported |= filename not in dependencies.requirement_files
            else:
                # Names alone cannot establish equivalent versions, extras, or markers.
                unsupported |= line not in dependencies.requirements
    if unsupported:
        raise ProfileError(
            "tox.ini test dependencies are not already covered by the pip acquisition; "
            "move them to requirements-test.txt/requirements-dev.txt or declared test/dev extras; "
            "tox extras and dependency_groups remain unsupported"
        )


def _pip_test_runner(
    root: Path,
    metadata: dict[str, object],
    setup: configparser.ConfigParser,
    dependencies: _PipDependencies,
) -> tuple[str, ...]:
    """Select only recognized runners; never execute arbitrary tox commands."""

    tool = _table(metadata.get("tool", {}), "tool")
    pytest_config = "ini_options" in _table(tool.get("pytest", {}), "tool.pytest")
    pytest_config |= setup.has_section("tool:pytest")
    for filename in ("pytest.ini", ".pytest.ini"):
        pytest_config |= _python_ini(root, filename).has_section("pytest")
    tox = _python_ini(root, "tox.ini")
    pytest_config |= tox.has_section("pytest")
    _check_tox_dependencies(tox, dependencies)
    runner: str | None = None
    if tox.has_option("testenv", "commands"):
        commands = tuple(
            " ".join(line.split())
            for line in tox.get("testenv", "commands").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        if commands in {("pytest",), ("python -m pytest",)}:
            runner = "pytest"
        elif commands == ("python -m unittest discover -s tests",):
            runner = "unittest"
        else:
            raise ProfileError(
                "unsupported tox.ini testenv commands; recognized runners are pytest, "
                "python -m pytest, or python -m unittest discover -s tests"
            )
    if runner == "unittest":
        if pytest_config:
            raise ProfileError("conflicting pytest and unittest runner configuration; select one")
        return ("-m", "unittest", "discover", "-s", "tests")
    if "pytest" in dependencies.names:
        return ("-m", "pytest")
    if runner == "pytest" or pytest_config:
        raise ProfileError(
            "pytest is configured but no unconditional pytest dependency is declared; "
            "add pytest to requirements-test.txt/requirements-dev.txt or a test/dev extra"
        )
    raise ProfileError(
        "cannot infer a supported Python test runner and its test dependencies; declare pytest "
        "in requirements-test.txt/requirements-dev.txt or a test/dev extra, or configure "
        "tox.ini [testenv] commands = python -m unittest discover -s tests for a stdlib suite"
    )


def _pip_profile_args(root: Path, *, with_tests: bool) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not with_tests:
        return ("-m", "pip", "install", "-e", "."), ("-m", "pytest")
    metadata = _python_metadata(root)
    setup = _python_ini(root, "setup.cfg")
    dependencies = _pip_test_dependencies(root, metadata, setup)
    return dependencies.install_args, _pip_test_runner(root, metadata, setup, dependencies)


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
    has_project = (
        _python_file(root, "pyproject.toml") is not None
        or _python_file(root, "setup.py") is not None
    )
    if not has_project:
        raise ProfileError("a Python profile requires pyproject.toml or setup.py")

    has_tests = any(
        item.path.startswith("tests/") and not item.is_symlink for item in summary.files
    )
    limitations: list[str] = []
    if profile == PYTHON_UV_PROFILE:
        if _python_file(root, "pyproject.toml") is None or _python_file(root, "uv.lock") is None:
            message = "python-uv requires both pyproject.toml and uv.lock"
            try:
                _pip_profile_args(root, with_tests=include_tests and has_tests)
            except ProfileError as error:
                raise ProfileError(
                    f"{message}; before selecting --profile python-pip-editable, "
                    f"resolve its test dependencies/profile requirements: {error}"
                ) from error
            raise ProfileError(
                f"{message}; for editable pip, explicitly select --profile python-pip-editable "
                "after reviewing its declared test dependencies, or provide the required uv files"
            )
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
        install_args, test_args = _pip_profile_args(root, with_tests=include_tests and has_tests)
        install = _command(
            "python",
            install_args,
            phase=PlanPhase.ACQUISITION,
            network_required=True,
            purpose="Install the Python project in editable mode (explicit profile)",
            timeout_seconds=timeout,
        )
        test_executable = "python"
        limitations.append(
            "The editable pip profile was explicitly selected; it is not an implicit fallback."
        )
        limitations.append(
            "On Docker, pip installs into a workspace-local Python user base shared across "
            "phase containers; verification uses that user site and its console-script PATH. "
            "The image must support Python user-site packages."
        )

    commands = [install]
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
    implicit fallback. With tests present, pip requires statically declared
    test dependencies and a recognized pytest or unittest runner; PEP 735 test
    groups are rejected rather than confused with distribution extras. Node
    planning accepts only ``npm ci`` and scripts exposed by ``package.json``.
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
        if (
            Ecosystem.PYTHON in detected
            and _python_file(root, "uv.lock") is not None
            and _python_file(root, "pyproject.toml") is not None
        ):
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
