"""Safe, side-effect-free repository discovery for the ReproCheck MVP.

Discovery only inspects filesystem metadata and file bytes.  It never runs
repository commands and never follows a symlink found below the repository
root.  Symlinks are returned as metadata entries, while their targets are
excluded from the discovered content and from any ecosystem inference.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Final

from .models import DiscoverySummary, Ecosystem, RepoFile, RepositoryIdentity

IGNORED_DIRECTORY_NAMES: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        ".pytest_cache",
        "coverage",
        ".ruff_cache",
        ".mypy_cache",
        ".hypothesis",
        ".tox",
        ".nox",
        ".pytype",
        "htmlcov",
    }
)

_ECOSYSTEM_ORDER: Final[tuple[Ecosystem, ...]] = (
    Ecosystem.PYTHON,
    Ecosystem.NODE,
    Ecosystem.CONTAINER,
    Ecosystem.GO,
    Ecosystem.RUST,
    Ecosystem.JAVA,
    Ecosystem.DOTNET,
    Ecosystem.RUBY,
    Ecosystem.PHP,
    Ecosystem.ELIXIR,
)

_UNSUPPORTED_MARKERS: Final[dict[Ecosystem, frozenset[str]]] = {
    Ecosystem.GO: frozenset({"go.mod", "go.sum"}),
    Ecosystem.RUST: frozenset({"cargo.toml", "cargo.lock"}),
    Ecosystem.JAVA: frozenset(
        {"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"}
    ),
    Ecosystem.RUBY: frozenset({"gemfile", "gemfile.lock"}),
    Ecosystem.PHP: frozenset({"composer.json", "composer.lock"}),
    Ecosystem.ELIXIR: frozenset({"mix.exs", "mix.lock"}),
}

_LANGUAGE_BY_SUFFIX: Final[dict[str, str]] = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".ex": "elixir",
    ".exs": "elixir",
    ".go": "go",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".json": "json",
    ".php": "php",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".sh": "shell",
    ".sql": "sql",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
}


def resolve_repository_root(repository: Path) -> Path:
    """Resolve and validate a repository root.

    A missing path raises :class:`FileNotFoundError`; an existing path that
    is not a directory raises :class:`NotADirectoryError`.  Symlinks in the
    root argument itself are resolved, but symlinks encountered during the
    walk are never followed.
    """

    root_argument = Path(repository)
    try:
        root = root_argument.expanduser().resolve(strict=True)
    except RuntimeError as error:
        raise ValueError(f"repository path cannot be resolved: {root_argument}") from error
    if not root.is_dir():
        raise NotADirectoryError(f"repository root is not a directory: {root_argument}")
    return root


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _relative_posix_path(root: Path, candidate: Path) -> str:
    """Return a safe, repository-relative POSIX path or reject an escape."""

    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(f"path escapes repository root: {candidate}") from error
    if relative == Path(".") or relative.is_absolute():
        raise ValueError(f"invalid repository-relative path: {candidate}")
    return relative.as_posix()


def _symlink_is_external(root: Path, path: Path) -> bool:
    """Determine whether a symlink target resolves outside ``root``.

    Resolution is used only for classification.  The target is never opened
    or traversed.  Broken links and links that cannot be resolved safely are
    classified as external so they remain ignored.
    """

    try:
        target = path.resolve(strict=False)
    except (OSError, RuntimeError):
        return True
    return not _is_within(root, target)


def _open_regular_file(path: Path) -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags)


def _file_digest(path: Path) -> tuple[int, str]:
    """Read a regular file without permitting a symlink substitution."""

    file_descriptor = _open_regular_file(path)
    try:
        with os.fdopen(file_descriptor, "rb") as stream:
            file_status = os.fstat(stream.fileno())
            if not stat.S_ISREG(file_status.st_mode):
                raise OSError(f"repository entry is not a regular file: {path}")
            digest = sha256()
            size = 0
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
            return size, digest.hexdigest()
    except BaseException:
        # ``fdopen`` takes ownership after it succeeds.  If it fails before
        # entering the context manager, close the descriptor here.
        try:
            os.close(file_descriptor)
        except OSError:
            pass
        raise


def _language_for_path(path: str) -> str | None:
    name = PurePosixPath(path).name.lower()
    if name == "dockerfile" or name.startswith("dockerfile."):
        return "dockerfile"
    if _is_compose_name(name):
        return "docker-compose"
    return _LANGUAGE_BY_SUFFIX.get(PurePosixPath(name).suffix)


def _is_compose_name(name: str) -> bool:
    return name.endswith((".yml", ".yaml")) and (
        name.startswith("docker-compose")
        or name in {"compose.yml", "compose.yaml"}
        or name.startswith("compose.")
    )


def _scan_directory(root: Path, directory: Path) -> tuple[list[RepoFile], int]:
    files: list[RepoFile] = []
    ignored_files = 0
    with os.scandir(directory) as entries:
        sorted_entries = sorted(entries, key=lambda entry: entry.name)
        for entry in sorted_entries:
            path = Path(entry.path)
            relative_path = _relative_posix_path(root, path)

            if entry.is_symlink():
                external = _symlink_is_external(root, path)
                files.append(
                    RepoFile(
                        path=relative_path,
                        language="symlink-external" if external else "symlink",
                        is_symlink=True,
                    )
                )
                ignored_files += 1
                continue

            if entry.is_dir(follow_symlinks=False):
                if entry.name in IGNORED_DIRECTORY_NAMES:
                    continue
                nested_files, nested_ignored = _scan_directory(root, path)
                files.extend(nested_files)
                ignored_files += nested_ignored
                continue

            if entry.is_file(follow_symlinks=False):
                size, digest = _file_digest(path)
                files.append(
                    RepoFile(
                        path=relative_path,
                        size_bytes=size,
                        sha256=digest,
                        language=_language_for_path(relative_path),
                    )
                )
                continue

            # Sockets, devices, FIFOs, and other non-regular entries are
            # ignored without opening them.
            ignored_files += 1
    return files, ignored_files


def _is_python_marker(name: str) -> bool:
    return name in {"pyproject.toml", "setup.py", "pipfile", "poetry.lock", "uv.lock"} or (
        name.startswith("requirements") and name.endswith(".txt")
    )


def _is_node_marker(name: str) -> bool:
    return name in {"package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"}


def _detect_ecosystems(files: Iterable[RepoFile]) -> tuple[Ecosystem, ...]:
    detected: set[Ecosystem] = set()
    for repo_file in files:
        if repo_file.is_symlink:
            continue
        name = PurePosixPath(repo_file.path).name.lower()
        if _is_python_marker(name):
            detected.add(Ecosystem.PYTHON)
        if _is_node_marker(name) or repo_file.language in {"javascript", "typescript"}:
            detected.add(Ecosystem.NODE)
        if name == "dockerfile" or name.startswith("dockerfile.") or _is_compose_name(name):
            detected.add(Ecosystem.CONTAINER)
        for ecosystem, markers in _UNSUPPORTED_MARKERS.items():
            if name in markers:
                detected.add(ecosystem)
        if name.endswith((".csproj", ".fsproj", ".vbproj", ".sln")):
            detected.add(Ecosystem.DOTNET)

    return tuple(ecosystem for ecosystem in _ECOSYSTEM_ORDER if ecosystem in detected)


def discover_repository(repository: Path) -> DiscoverySummary:
    """Discover regular files, safe symlink metadata, and marker ecosystems.

    The returned file paths are sorted repository-relative POSIX paths.  Files
    beneath :data:`IGNORED_DIRECTORY_NAMES` are not visited.  Symlinks are
    included with ``is_symlink=True`` and a ``None`` size/digest; their targets
    are never part of the result.  ``ignored_files`` counts symlinks and
    non-regular filesystem entries encountered during the walk.
    """

    root = resolve_repository_root(repository)
    files, ignored_files = _scan_directory(root, root)
    files.sort(key=lambda repo_file: repo_file.path)
    return DiscoverySummary(
        files=tuple(files),
        ecosystems=_detect_ecosystems(files),
        root=str(root),
        total_files=len(files),
        ignored_files=ignored_files,
    )


def build_repository_identity(repository: Path) -> RepositoryIdentity:
    """Build a filesystem-only identity without invoking repository commands.

    Git revision, branch, remote, and dirty-state metadata remain unavailable
    by design.  Callers can attach such metadata later when they already have
    a trusted repository integration.
    """

    root = resolve_repository_root(repository)
    return RepositoryIdentity(root=str(root))


def repository_identity(repository: Path) -> RepositoryIdentity:
    """Alias for :func:`build_repository_identity`."""

    return build_repository_identity(repository)


__all__ = [
    "IGNORED_DIRECTORY_NAMES",
    "build_repository_identity",
    "discover_repository",
    "repository_identity",
    "resolve_repository_root",
]
