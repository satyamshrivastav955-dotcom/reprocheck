"""Safe, regular-file-only temporary workspaces for verification."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..discovery import discover_repository, resolve_repository_root


class WorkspaceError(RuntimeError):
    """Raised when a repository cannot be safely materialized."""


def _safe_relative_path(value: str) -> PurePosixPath:
    relative = PurePosixPath(value)
    if (
        not value
        or relative.is_absolute()
        or relative == PurePosixPath(".")
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise WorkspaceError(f"invalid repository-relative path: {value!r}")
    return relative


def _contained_path(root: Path, relative: str) -> Path:
    parts = _safe_relative_path(relative).parts
    candidate = root.joinpath(*parts)
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=True))
    except (OSError, RuntimeError, ValueError) as error:
        raise WorkspaceError(f"path escapes workspace root: {relative!r}") from error
    return candidate


def _open_regular_source(path: Path) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise WorkspaceError(f"cannot open repository file safely: {path}") from error
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            os.close(descriptor)
            raise WorkspaceError(f"repository entry is not a regular file: {path}")
        return descriptor, status
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _copy_regular_file(source: Path, destination: Path, mode: int) -> None:
    descriptor, status = _open_regular_source(source)
    try:
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            mode,
        )
    except OSError as error:
        os.close(descriptor)
        raise WorkspaceError(f"cannot create workspace file: {destination}") from error

    try:
        with (
            os.fdopen(descriptor, "rb") as source_stream,
            os.fdopen(destination_descriptor, "wb") as destination_stream,
        ):
            copied = 0
            while True:
                chunk = source_stream.read(1024 * 1024)
                if not chunk:
                    break
                destination_stream.write(chunk)
                copied += len(chunk)
            if copied != status.st_size:
                raise WorkspaceError(f"repository file changed while copying: {source}")
        os.chmod(destination, stat.S_IMODE(status.st_mode))
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.close(destination_descriptor)
        except OSError:
            pass
        destination.unlink(missing_ok=True)
        raise


@dataclass
class MaterializedWorkspace:
    """A materialized workspace that removes itself when its context exits."""

    path: Path
    source: Path
    _owned: bool = True

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if self._owned:
            shutil.rmtree(self.path, ignore_errors=True)


def materialize_workspace(
    source: Path,
    *,
    destination: Path | None = None,
) -> MaterializedWorkspace:
    """Copy discovered regular files into a contained temporary directory.

    Symlink entries are rejected rather than followed or copied.  Ignored
    discovery directories and non-regular filesystem entries are not copied.
    Every source and destination path is checked again immediately before it is
    opened, so a path substitution cannot turn a copy into an escape.
    """

    root = resolve_repository_root(source)
    summary = discover_repository(root)
    symlinks = tuple(item.path for item in summary.files if item.is_symlink)
    if symlinks:
        raise WorkspaceError(
            f"symlink entries are not allowed in a verification workspace: {symlinks}"
        )

    if destination is None:
        workspace_path = Path(tempfile.mkdtemp(prefix="reprocheck-workspace-"))
        owned = True
    else:
        workspace_path = Path(destination)
        if workspace_path.exists() and workspace_path.is_symlink():
            raise WorkspaceError("workspace destination cannot be a symlink")
        if workspace_path.exists() and not workspace_path.is_dir():
            raise WorkspaceError("workspace destination is not a directory")
        workspace_path.mkdir(parents=True, exist_ok=True)
        if any(workspace_path.iterdir()):
            raise WorkspaceError("workspace destination must be empty")
        owned = False

    try:
        workspace_root = workspace_path.resolve(strict=True)
        for repo_file in summary.files:
            source_path = _contained_path(root, repo_file.path)
            destination_path = _contained_path(workspace_root, repo_file.path)
            if source_path.is_symlink():
                raise WorkspaceError(f"symlink entry is not allowed: {repo_file.path}")
            source_status = os.lstat(source_path)
            if not stat.S_ISREG(source_status.st_mode):
                raise WorkspaceError(f"source entry is not a regular file: {repo_file.path}")
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            parent_resolved = destination_path.parent.resolve(strict=True)
            parent_resolved.relative_to(workspace_root)
            if destination_path.exists() or destination_path.is_symlink():
                raise WorkspaceError(f"workspace destination collision: {repo_file.path}")
            _copy_regular_file(source_path, destination_path, stat.S_IMODE(source_status.st_mode))
    except BaseException:
        if owned:
            shutil.rmtree(workspace_path, ignore_errors=True)
        raise

    return MaterializedWorkspace(path=workspace_path, source=root, _owned=owned)


__all__ = ["MaterializedWorkspace", "WorkspaceError", "materialize_workspace"]
