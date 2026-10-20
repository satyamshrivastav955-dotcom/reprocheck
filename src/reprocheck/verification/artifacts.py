"""Safe, deterministic hashing of explicitly selected verification artifacts."""

from __future__ import annotations

import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath, PureWindowsPath

from ..models import JSONValue

ARTIFACT_FINGERPRINT_ALGORITHM = "artifacts-v1"


class ArtifactError(ValueError):
    """Raised when an artifact selection is unsafe or cannot be inspected."""


@dataclass(frozen=True)
class ArtifactFile:
    """One regular file included in an artifact manifest."""

    path: str
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class ArtifactManifest:
    """A bounded, timestamp-free manifest for selected workspace output."""

    algorithm: str
    requested_paths: tuple[str, ...]
    files: tuple[ArtifactFile, ...]
    missing: tuple[str, ...]
    complete: bool
    digest: str | None

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "algorithm": self.algorithm,
            "requested_paths": list(self.requested_paths),
            "files": [item.to_dict() for item in self.files],
            "missing": list(self.missing),
            "complete": self.complete,
            "digest": self.digest,
        }


def _relative_path(value: str) -> PurePosixPath:
    if not isinstance(value, str):
        raise ArtifactError("artifact path must be a string")
    normalized = value.replace("\\", "/")
    relative = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if (
        not normalized
        or "\x00" in normalized
        or relative.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or relative == PurePosixPath(".")
        or relative.as_posix() != normalized
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ArtifactError(f"artifact path must be workspace-relative: {value!r}")
    return relative


def normalize_artifact_paths(paths: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Validate and sort explicit artifact paths once before execution."""

    return tuple(sorted({_relative_path(value).as_posix() for value in paths}))


def _contained_path(root: Path, relative: PurePosixPath) -> Path:
    candidate = root.joinpath(*relative.parts)
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=True))
    except (OSError, RuntimeError, ValueError) as error:
        raise ArtifactError(f"artifact path escapes workspace: {relative.as_posix()!r}") from error
    return candidate


def _file_digest(path: Path) -> tuple[int, str]:
    digest = sha256()
    size = 0
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
    except OSError as error:
        raise ArtifactError(f"cannot open artifact safely: {path}") from error
    try:
        with os.fdopen(descriptor, "rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    return size, digest.hexdigest()


def _regular_files(root: Path, relative: PurePosixPath) -> tuple[PurePosixPath, ...]:
    candidate = _contained_path(root, relative)
    if candidate.is_symlink():
        raise ArtifactError(f"artifact path is a symlink: {relative.as_posix()!r}")
    if candidate.is_file():
        return (relative,)
    if not candidate.is_dir():
        raise ArtifactError(
            f"artifact path is not a regular file or directory: {relative.as_posix()!r}"
        )

    files: list[PurePosixPath] = []
    with os.scandir(candidate) as entries:
        for entry in sorted(entries, key=lambda item: item.name):
            child = relative / entry.name
            if entry.is_symlink():
                raise ArtifactError(f"artifact contains a symlink: {child.as_posix()!r}")
            if entry.is_dir(follow_symlinks=False):
                files.extend(_regular_files(root, child))
            elif entry.is_file(follow_symlinks=False):
                files.append(child)
            else:
                raise ArtifactError(f"artifact contains a non-regular entry: {child.as_posix()!r}")
    return tuple(files)


def _aggregate_digest(files: tuple[ArtifactFile, ...]) -> str:
    digest = sha256()
    for item in files:
        path_bytes = item.path.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(item.size_bytes.to_bytes(8, "big"))
        digest.update(bytes.fromhex(item.sha256))
    return digest.hexdigest()


def collect_artifacts(workspace: Path, paths: tuple[str, ...] | list[str]) -> ArtifactManifest:
    """Collect an explicit artifact selection without following symlinks."""

    normalized = normalize_artifact_paths(paths)
    if not normalized:
        return ArtifactManifest(ARTIFACT_FINGERPRINT_ALGORITHM, (), (), (), False, None)

    files_by_path: dict[str, Path] = {}
    missing: list[str] = []
    root = workspace.resolve(strict=True)
    for value in normalized:
        relative = PurePosixPath(value)
        candidate = _contained_path(root, relative)
        if candidate.is_symlink():
            raise ArtifactError(f"artifact path is a symlink: {value!r}")
        if not candidate.exists():
            missing.append(value)
            continue
        for file_relative in _regular_files(root, relative):
            files_by_path[file_relative.as_posix()] = _contained_path(root, file_relative)

    artifact_files: list[ArtifactFile] = []
    for value in sorted(files_by_path):
        size, file_hash = _file_digest(files_by_path[value])
        artifact_files.append(ArtifactFile(value, size, file_hash))
    collected = tuple(artifact_files)
    complete = not missing and bool(collected)
    return ArtifactManifest(
        algorithm=ARTIFACT_FINGERPRINT_ALGORITHM,
        requested_paths=normalized,
        files=collected,
        missing=tuple(missing),
        complete=complete,
        digest=_aggregate_digest(collected) if complete else None,
    )


def empty_artifact_manifest(paths: tuple[str, ...] | list[str]) -> ArtifactManifest:
    """Return an incomplete manifest when a run never reached workspace observation."""

    normalized = normalize_artifact_paths(paths)
    return ArtifactManifest(ARTIFACT_FINGERPRINT_ALGORITHM, normalized, (), (), False, None)


def artifacts_match(first: ArtifactManifest, second: ArtifactManifest) -> bool:
    """Return whether two complete, explicitly selected manifests match."""

    return (
        first.complete
        and second.complete
        and first.requested_paths == second.requested_paths
        and first.digest is not None
        and first.digest == second.digest
    )


__all__ = [
    "ARTIFACT_FINGERPRINT_ALGORITHM",
    "ArtifactError",
    "ArtifactFile",
    "ArtifactManifest",
    "artifacts_match",
    "collect_artifacts",
    "empty_artifact_manifest",
    "normalize_artifact_paths",
]
