"""Bounded redaction for command output and receipt evidence."""

from __future__ import annotations

import re

DEFAULT_MAX_LOG_CHARS = 32 * 1024
TRUNCATION_MARKER = "<TRUNCATED>"

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-\n]*PRIVATE KEY-----.*?-----END [^-\n]*PRIVATE KEY-----",
    re.DOTALL,
)
_ASSIGNMENT_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret(?:[_-]?key)?|"
    r"password|private[_-]?key|client[_-]?secret)\b\s*[:=]\s*)([^\s,;]+)"
)
_URL_CREDENTIAL_RE = re.compile(r"(?i)(https?://)([^/@\s]+):([^/@\s]+)@")
_KNOWN_TOKEN_RE = re.compile(
    r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{20,}|sk-[A-Za-z0-9_-]{20,})\b"
)
_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)+|"
    r"[A-Za-z]:\\[^\s\"']+)"
)


def redact_text(value: str) -> str:
    """Redact likely secrets, credentials, absolute paths, and NUL bytes."""

    redacted = _PRIVATE_KEY_RE.sub("<REDACTED_PRIVATE_KEY>", value)
    redacted = _URL_CREDENTIAL_RE.sub(r"\1<REDACTED_USER>:<REDACTED_PASSWORD>@", redacted)
    redacted = _ASSIGNMENT_SECRET_RE.sub(r"\1<REDACTED_VALUE>", redacted)
    redacted = _KNOWN_TOKEN_RE.sub("<REDACTED_TOKEN>", redacted)
    redacted = _ABSOLUTE_PATH_RE.sub("<ABSOLUTE_PATH>", redacted)
    return redacted.replace("\x00", "<NUL>")


def bounded_redact(value: str, *, max_chars: int = DEFAULT_MAX_LOG_CHARS) -> str:
    """Redact a value and retain at most ``max_chars`` characters.

    The marker is included in the bound, making the size of serialized logs
    predictable even when a process writes unbounded output.
    """

    if max_chars < 0:
        raise ValueError("max_chars must be non-negative")
    redacted = redact_text(value)
    if len(redacted) <= max_chars:
        return redacted
    if max_chars <= len(TRUNCATION_MARKER):
        return TRUNCATION_MARKER[:max_chars]
    return redacted[: max_chars - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


def redact_output(value: str, *, max_chars: int = DEFAULT_MAX_LOG_CHARS) -> str:
    """Compatibility name for bounded command-output redaction."""

    return bounded_redact(value, max_chars=max_chars)


__all__ = [
    "DEFAULT_MAX_LOG_CHARS",
    "TRUNCATION_MARKER",
    "bounded_redact",
    "redact_output",
    "redact_text",
]
