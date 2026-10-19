"""Deterministic filesystem content fingerprints for ReproCheck."""

from __future__ import annotations

import os
import stat
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Protocol

from .discovery import _open_regular_file, discover_repository, resolve_repository_root
from .models import ContentFingerprint, RepoFile

FILESYSTEM_FINGERPRINT_ALGORITHM = "filesystem-v1"


class _Digest(Protocol):
    def update(self, data: bytes, /) -> None:
        """Add bytes to a digest."""


def _safe_file_path(root: Path, repo_file: RepoFile) -> Path:
    """Resolve a discovered relative path while rejecting path escapes."""

    relative = PurePosixPath(repo_file.path)
    if (
        not repo_file.path
        or relative.is_absolute()
        or relative == PurePosixPath(".")
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.as_posix() != repo_file.path
    ):
        raise ValueError(f"invalid repository-relative path: {repo_file.path!r}")

    candidate = root.joinpath(*relative.parts)
    try:
        resolved_candidate = candidate.resolve(strict=False)
        resolved_candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError(f"path escapes repository root: {repo_file.path!r}") from error
    return candidate


def _update_file_record(digest: _Digest, repo_file: RepoFile, path: Path) -> None:
    """Add one path-and-bytes record using the filesystem-v1 framing."""

    path_bytes = repo_file.path.encode("utf-8")
    file_descriptor = _open_regular_file(path)
    try:
        with os.fdopen(file_descriptor, "rb") as stream:
            file_status = os.fstat(stream.fileno())
            if not stat.S_ISREG(file_status.st_mode):
                raise OSError(f"repository entry is not a regular file: {path}")

            digest.update(len(path_bytes).to_bytes(8, "big"))
            digest.update(path_bytes)
            digest.update(file_status.st_size.to_bytes(8, "big"))

            bytes_read = 0
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                bytes_read += len(chunk)
                digest.update(chunk)
            if bytes_read != file_status.st_size:
                raise OSError(f"repository file changed while reading: {path}")
    except BaseException:
        try:
            os.close(file_descriptor)
        except OSError:
            pass
        raise


def fingerprint_repository(repository: Path) -> ContentFingerprint:
    """Return a deterministic SHA-256 fingerprint of repository file content.

    ``filesystem-v1`` sorts canonical relative POSIX paths, then hashes each
    regular file as a length-prefixed path, a length-prefixed byte count, and
    the file bytes.  Skipped directories, symlinks, and symlink targets are
    absent from the manifest.  The implementation validates every generated
    path again before opening it so a path substitution cannot escape the
    resolved repository root.
    """

    root = resolve_repository_root(repository)
    summary = discover_repository(root)
    digest = sha256()
    regular_files = (repo_file for repo_file in summary.files if not repo_file.is_symlink)
    file_count = 0
    for repo_file in regular_files:
        path = _safe_file_path(root, repo_file)
        _update_file_record(digest, repo_file, path)
        file_count += 1
    return ContentFingerprint(
        algorithm=FILESYSTEM_FINGERPRINT_ALGORITHM,
        digest=digest.hexdigest(),
        file_count=file_count,
    )


def filesystem_fingerprint(repository: Path) -> ContentFingerprint:
    """Alias for :func:`fingerprint_repository`."""

    return fingerprint_repository(repository)


def compute_content_fingerprint(repository: Path) -> ContentFingerprint:
    """Alias for :func:`fingerprint_repository`."""

    return fingerprint_repository(repository)


__all__ = [
    "FILESYSTEM_FINGERPRINT_ALGORITHM",
    "compute_content_fingerprint",
    "filesystem_fingerprint",
    "fingerprint_repository",
]
