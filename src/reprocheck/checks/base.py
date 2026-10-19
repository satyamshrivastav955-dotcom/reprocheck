"""Contracts and bounded filesystem helpers for static repository checks.

Checks intentionally consume a discovery result instead of walking arbitrary
paths.  This keeps the audit side-effect free and means a check cannot follow
a repository symlink or accidentally inspect a path outside the repository.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

from ..models import (
    Assessment,
    CheckSpec,
    DiscoverySummary,
    Ecosystem,
    Evidence,
    Finding,
    JSONValue,
    RepoFile,
    ResultState,
    Severity,
    VerificationClass,
)

MAX_TEXT_BYTES = 1024 * 1024
ReadStatus = Literal["ok", "missing", "inaccessible", "malformed"]


@dataclass(frozen=True, init=False)
class CheckContext:
    """Inputs available to one static check.

    ``repository_root`` is the canonical field.  The ``root`` keyword and
    property are accepted as a small compatibility convenience because the
    discovery model calls its corresponding field ``root``.
    """

    repository_root: Path
    discovery: DiscoverySummary
    configuration: Mapping[str, object] | None

    def __init__(
        self,
        repository_root: Path | None = None,
        discovery: DiscoverySummary | None = None,
        configuration: Mapping[str, object] | None = None,
        *,
        root: Path | None = None,
    ) -> None:
        selected_root = repository_root if repository_root is not None else root
        if selected_root is None:
            raise TypeError("CheckContext requires repository_root")
        if discovery is None:
            raise TypeError("CheckContext requires discovery")
        object.__setattr__(self, "repository_root", Path(selected_root))
        object.__setattr__(self, "discovery", discovery)
        object.__setattr__(self, "configuration", configuration)

    @property
    def root(self) -> Path:
        """Return the repository root using discovery-compatible naming."""

        return self.repository_root


class Check(Protocol):
    """Interface implemented by every static audit check."""

    spec: CheckSpec

    def evaluate(self, context: CheckContext) -> tuple[Finding, ...]:
        """Evaluate the check without executing repository-controlled code."""


@dataclass(frozen=True)
class ReadResult:
    """Bounded result of reading one discovered repository text file."""

    status: ReadStatus
    text: str = ""
    error: str | None = None


class BaseCheck:
    """Small common base class for concrete checks."""

    spec: CheckSpec

    def evaluate(self, context: CheckContext) -> tuple[Finding, ...]:
        raise NotImplementedError


def safe_relative_path(path: str) -> bool:
    """Return whether ``path`` is a canonical repository-relative POSIX path."""

    relative = PurePosixPath(path)
    return bool(
        path
        and not relative.is_absolute()
        and relative != PurePosixPath(".")
        and relative.as_posix() == path
        and all(part not in {"", ".", ".."} for part in relative.parts)
    )


def known_files(context: CheckContext) -> dict[str, RepoFile]:
    """Return safe discovered file records keyed by canonical relative path."""

    result: dict[str, RepoFile] = {}
    for repo_file in context.discovery.files:
        if safe_relative_path(repo_file.path) and repo_file.path not in result:
            result[repo_file.path] = repo_file
    return result


def top_level_paths(context: CheckContext) -> tuple[str, ...]:
    """Return safe discovered paths directly below the repository root."""

    return tuple(
        sorted(path for path in known_files(context) if len(PurePosixPath(path).parts) == 1)
    )


def path_basename(path: str) -> str:
    """Return a POSIX repository path's case-normalized basename."""

    return PurePosixPath(path).name.lower()


def read_known_text(context: CheckContext, relative_path: str) -> ReadResult:
    """Read one discovered regular file with size, path, and symlink bounds."""

    if not safe_relative_path(relative_path):
        return ReadResult("inaccessible", error="invalid repository-relative path")
    repo_file = known_files(context).get(relative_path)
    if repo_file is None:
        return ReadResult("missing")
    if getattr(repo_file, "is_symlink", False):
        return ReadResult("inaccessible", error="symlink content is not inspected")
    size_bytes = getattr(repo_file, "size_bytes", None)
    if isinstance(size_bytes, int) and size_bytes > MAX_TEXT_BYTES:
        return ReadResult("inaccessible", error="file exceeds bounded static read size")

    root = context.repository_root
    try:
        resolved_root = root.resolve(strict=True)
        candidate = root.joinpath(*PurePosixPath(relative_path).parts)
        resolved_candidate = candidate.resolve(strict=False)
        resolved_candidate.relative_to(resolved_root)
        if candidate.is_symlink() or not candidate.is_file():
            return ReadResult("inaccessible", error="repository entry is not a regular file")

        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(candidate, flags)
        try:
            with os.fdopen(descriptor, "rb") as stream:
                content = stream.read(MAX_TEXT_BYTES + 1)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
    except (OSError, RuntimeError, ValueError) as error:
        return ReadResult("inaccessible", error=str(error))

    if len(content) > MAX_TEXT_BYTES:
        return ReadResult("inaccessible", error="file exceeds bounded static read size")
    try:
        return ReadResult("ok", text=content.decode("utf-8"))
    except UnicodeDecodeError as error:
        return ReadResult("malformed", error=f"file is not valid UTF-8: {error.reason}")


def text_file_paths(context: CheckContext) -> tuple[str, ...]:
    """Return discovered paths likely to contain auditable text."""

    text_suffixes = {
        ".cfg",
        ".conf",
        ".env",
        ".gitignore",
        ".ini",
        ".json",
        ".md",
        ".py",
        ".rst",
        ".sh",
        ".toml",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
    paths: list[str] = []
    for path in known_files(context):
        name = PurePosixPath(path).name.lower()
        suffix = PurePosixPath(name).suffix
        if suffix in text_suffixes or not suffix or name.startswith("."):
            paths.append(path)
    return tuple(sorted(paths))


_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-\n]*PRIVATE KEY-----.*?-----END [^-\n]*PRIVATE KEY-----",
    re.DOTALL,
)
_ASSIGNMENT_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret(?:[_-]?key)?|password|private[_-]?key)\b\s*[:=]\s*)([^\s,;]+)"
)
_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)+|"
    r"[A-Za-z]:\\[^\s\"']+)"
)
_KNOWN_TOKEN_RE = re.compile(
    r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{20,})\b"
)


def redact_excerpt(value: str) -> str:
    """Redact likely secrets and machine paths from a short evidence excerpt."""

    redacted = _PRIVATE_KEY_RE.sub("<REDACTED_PRIVATE_KEY>", value)
    redacted = _ASSIGNMENT_SECRET_RE.sub(r"\1<REDACTED_VALUE>", redacted)
    redacted = _KNOWN_TOKEN_RE.sub("<REDACTED_TOKEN>", redacted)
    redacted = _ABSOLUTE_PATH_RE.sub("<ABSOLUTE_PATH>", redacted)
    redacted = redacted.replace("\x00", "<NUL>").strip()
    return redacted[:400]


def excerpt_for_line(text: str, line_number: int | None = None) -> str:
    """Return a bounded, redacted excerpt from text or one selected line."""

    if line_number is not None:
        lines = text.splitlines()
        if 1 <= line_number <= len(lines):
            return redact_excerpt(lines[line_number - 1])
    return redact_excerpt(text[:400].replace("\n", " "))


def static_evidence(
    spec: CheckSpec,
    summary: str,
    *,
    source: str | None = None,
    excerpt: str | None = None,
    details: Mapping[str, JSONValue] | None = None,
) -> Evidence:
    """Build deterministic static evidence with relative source paths."""

    evidence_details: dict[str, JSONValue] = dict(details or {})
    if excerpt is not None:
        evidence_details["excerpt"] = redact_excerpt(excerpt)
    evidence_id = f"{spec.check_id.lower()}:{source or 'summary'}"
    return Evidence(
        id=evidence_id,
        verification_class=VerificationClass.STATIC,
        summary=summary,
        check_id=spec.check_id,
        details=evidence_details,
        source=source,
    )


def make_finding(
    spec: CheckSpec,
    *,
    title: str,
    message: str,
    assessment: Assessment,
    state: ResultState,
    evidence: Sequence[Evidence] = (),
    path: str | None = None,
    line: int | None = None,
    severity: Severity | None = None,
    code: str | None = None,
    remediation: str | None = None,
    verification_hint: str | None = None,
    ecosystems: Sequence[Ecosystem] = (),
    applicability_reason: str | None = None,
) -> Finding:
    """Create a finding carrying the metadata declared by its check spec."""

    return Finding(
        check_id=spec.check_id,
        title=title,
        message=message,
        assessment=assessment,
        severity=severity or spec.severity,
        category=spec.category,
        state=state,
        code=code,
        path=path,
        line=line,
        evidence=tuple(evidence),
        remediation=remediation,
        verification_hint=verification_hint,
        false_positive_policy=spec.false_positive_policy,
        ecosystems=tuple(ecosystems),
        applicability_reason=applicability_reason,
        scoring_group=spec.scoring_group,
    )


def unsupported_finding(
    spec: CheckSpec,
    *,
    title: str,
    message: str,
    reason: str,
) -> Finding:
    """Return the standard outcome for an inapplicable static check."""

    evidence = static_evidence(spec, reason, details={"reason": reason})
    return make_finding(
        spec,
        title=title,
        message=message,
        assessment=Assessment.UNSUPPORTED,
        state=ResultState.NOT_VERIFIED,
        evidence=(evidence,),
        applicability_reason=reason,
    )


def blocked_finding(
    spec: CheckSpec,
    *,
    title: str,
    message: str,
    reason: str,
    path: str | None = None,
) -> Finding:
    """Return the standard outcome for an inaccessible static input."""

    safe_reason = redact_excerpt(reason)
    evidence = static_evidence(spec, safe_reason, source=path, details={"reason": safe_reason})
    return make_finding(
        spec,
        title=title,
        message=message,
        assessment=Assessment.BLOCKED,
        state=ResultState.NOT_VERIFIED,
        evidence=(evidence,),
        path=path,
    )


def parse_error_finding(
    spec: CheckSpec,
    *,
    title: str,
    message: str,
    reason: str,
    path: str | None = None,
) -> Finding:
    """Return the standard outcome for malformed static input."""

    safe_reason = redact_excerpt(reason)
    evidence = static_evidence(spec, safe_reason, source=path, details={"reason": safe_reason})
    return make_finding(
        spec,
        title=title,
        message=message,
        assessment=Assessment.ERROR,
        state=ResultState.STATIC_FAIL,
        evidence=(evidence,),
        path=path,
    )


ALL_RUNTIME_ECOSYSTEMS: tuple[Ecosystem, ...] = (
    Ecosystem.PYTHON,
    Ecosystem.NODE,
    Ecosystem.GO,
    Ecosystem.RUST,
    Ecosystem.JAVA,
    Ecosystem.DOTNET,
    Ecosystem.RUBY,
    Ecosystem.PHP,
    Ecosystem.ELIXIR,
)
ALL_SUPPORTED_ECOSYSTEMS: tuple[Ecosystem, ...] = ALL_RUNTIME_ECOSYSTEMS + (Ecosystem.CONTAINER,)

__all__ = [
    "ALL_RUNTIME_ECOSYSTEMS",
    "ALL_SUPPORTED_ECOSYSTEMS",
    "BaseCheck",
    "Check",
    "CheckContext",
    "MAX_TEXT_BYTES",
    "ReadResult",
    "blocked_finding",
    "excerpt_for_line",
    "known_files",
    "make_finding",
    "parse_error_finding",
    "path_basename",
    "read_known_text",
    "redact_excerpt",
    "safe_relative_path",
    "static_evidence",
    "text_file_paths",
    "top_level_paths",
    "unsupported_finding",
]
