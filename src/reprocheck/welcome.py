"""A restrained, terminal-safe welcome screen for the bare ReproCheck command.

The renderer deliberately uses ASCII geometry only.  That keeps the lockup
readable in PowerShell, cmd.exe, POSIX terminals, redirected output, and
terminals with incomplete Unicode fonts.  ANSI color is an optional layer on
top of the same deterministic plain-text layout.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap
from typing import Protocol, TextIO, cast

_RESET = "\x1b[0m"
_BOLD_WHITE = "\x1b[1;97m"
_WHITE = "\x1b[97m"
_MUTED = "\x1b[90m"
_CYAN = "\x1b[96m"
_GREEN = "\x1b[92m"
_RED = "\x1b[91m"
_BLUE = "\x1b[94m"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_TAGLINE = "Repository verification. Evidence, not guesses."
_WORKFLOW = ("SCAN", "ISOLATE", "REPRODUCE", "VERIFY", "EVIDENCE")
_COMMANDS = (
    ("reprocheck scan .", "Inspect the repository without executing it."),
    (
        "reprocheck verify . --profile python-uv --backend docker",
        "Review the verification plan; add --approve to execute.",
    ),
    ("reprocheck receipt --help", "Validate and compare evidence receipts."),
)

# A compact five-row ASCII lockup is the closest portable equivalent to a
# larger terminal font.  It is intentionally typography, not an illustration.
_WORDMARK_FONT: dict[str, tuple[str, ...]] = {
    "R": ("#### ", "#   #", "#### ", "#  # ", "#   #"),
    "E": ("#####", "#    ", "#### ", "#    ", "#####"),
    "P": ("#### ", "#   #", "#### ", "#    ", "#    "),
    "O": (" ### ", "#   #", "#   #", "#   #", " ### "),
    "C": (" ####", "#    ", "#    ", "#    ", " ####"),
    "H": ("#   #", "#   #", "#####", "#   #", "#   #"),
    "K": ("#   #", "#  # ", "###  ", "#  # ", "#   #"),
}


class _ColumnStream(Protocol):
    columns: int


def _paint(value: str, code: str, *, enabled: bool) -> str:
    if not enabled or not value or not code:
        return value
    return f"{code}{value}{_RESET}"


def _visible_width(value: str) -> int:
    return len(_ANSI_RE.sub("", value))


def _fit(value: str, width: int) -> str:
    """Clip a plain value without ever returning a line wider than ``width``."""
    return value[: max(0, width)]


def color_enabled(stream: TextIO | None = None) -> bool:
    """Return whether ANSI color is appropriate for the output stream."""

    output = stream if stream is not None else sys.stdout
    return (
        bool(output.isatty())
        and "NO_COLOR" not in os.environ
        and os.environ.get("TERM", "").lower() != "dumb"
    )


def _terminal_width(stream: TextIO | None) -> int:
    if stream is not None and hasattr(stream, "columns"):
        columns = cast(_ColumnStream, stream).columns
        if isinstance(columns, int) and columns > 0:
            return columns
    return shutil.get_terminal_size(fallback=(100, 24)).columns


def _wrap(value: str, width: int) -> list[str]:
    return textwrap.wrap(
        value,
        width=max(1, width),
        break_long_words=True,
        break_on_hyphens=False,
        replace_whitespace=True,
        drop_whitespace=True,
    ) or [""]


def _header(version: str, width: int, *, enabled: bool) -> str:
    label = "R E P R O C H E C K" if width >= 28 else "REPROCHECK"
    version_label = f"v{version}"
    if width >= len(label) + len(version_label) + 6:
        rule = "-" * (width - len(label) - len(version_label) - 4)
        return (
            _paint(label, _MUTED, enabled=enabled)
            + "  "
            + _paint(rule, _MUTED, enabled=enabled)
            + "  "
            + _paint(version_label, _WHITE, enabled=enabled)
        )
    if width >= len(label) + len(version_label) + 1:
        return (
            _paint(label, _MUTED, enabled=enabled)
            + " "
            + _paint(version_label, _WHITE, enabled=enabled)
        )
    return _paint(_fit(label, width), _MUTED, enabled=enabled)


def _wordmark(width: int, *, enabled: bool) -> list[str]:
    """Render the prominent lockup, falling back cleanly on tiny terminals."""

    first = "REPRO"
    second = "CHECK"
    first_rows = [
        " ".join(_WORDMARK_FONT[letter][row] for letter in first).rstrip() for row in range(5)
    ]
    second_rows = [
        " ".join(_WORDMARK_FONT[letter][row] for letter in second).rstrip() for row in range(5)
    ]
    if (
        max(
            len(left) + 2 + len(right) for left, right in zip(first_rows, second_rows, strict=False)
        )
        <= width
    ):
        return [
            _paint(left, _BOLD_WHITE, enabled=enabled)
            + "  "
            + _paint(right, "\x1b[1;91m", enabled=enabled)
            for left, right in zip(first_rows, second_rows, strict=False)
        ]

    # Keep the real name available and readable where the block lockup cannot
    # fit.  This is also friendlier than shrinking type until it becomes noise.
    return [
        _paint("Repro", _BOLD_WHITE, enabled=enabled)
        + _paint("Check", "\x1b[1;91m", enabled=enabled)
    ]


def _workflow_lines(width: int, *, enabled: bool) -> list[str]:
    lines: list[str] = []
    current = ""
    for label in _WORKFLOW:
        candidate = label if not current else f"{current}  |  {label}"
        if current and len(candidate) > width:
            lines.append(current)
            current = label
        else:
            current = candidate
    if current:
        lines.append(current)

    rendered: list[str] = []
    for line in lines:
        pieces = line.split("  |  ")
        rendered.append("  |  ".join(_paint(piece, _WHITE, enabled=enabled) for piece in pieces))
    return rendered


def _style_command(value: str, *, enabled: bool) -> str:
    parts = value.split(" ", 2)
    if len(parts) < 2 or parts[0] != "reprocheck":
        return _paint(value, _WHITE, enabled=enabled)
    result = (
        _paint(parts[0], _WHITE, enabled=enabled) + " " + _paint(parts[1], _CYAN, enabled=enabled)
    )
    if len(parts) == 3:
        result += " " + _paint(parts[2], _WHITE, enabled=enabled)
    return result


def _panel_line(content: str, inner_width: int, *, enabled: bool, style: str = "") -> str:
    plain = _fit(content, inner_width)
    return _panel_rendered_line(
        plain, _paint(plain, style, enabled=enabled), inner_width, enabled=enabled
    )


def _panel_rendered_line(plain: str, rendered: str, inner_width: int, *, enabled: bool) -> str:
    return (
        _paint("|", _MUTED, enabled=enabled)
        + rendered
        + " " * max(0, inner_width - len(plain))
        + _paint("|", _MUTED, enabled=enabled)
    )


def _panel_command_lines(command: str, inner_width: int, *, enabled: bool) -> list[str]:
    available = max(1, inner_width - 4)
    chunks = _wrap(command, available)
    rows: list[str] = []
    for index, chunk in enumerate(chunks):
        prefix = "  > " if index == 0 else "    "
        rows.append(
            _panel_rendered_line(
                prefix + chunk,
                prefix + _style_command(chunk, enabled=enabled),
                inner_width,
                enabled=enabled,
            )
        )
    return rows


def _panel_rows(panel_width: int, *, enabled: bool) -> list[str]:
    inner = max(1, panel_width - 2)
    border = _paint("+" + "-" * inner + "+", _MUTED, enabled=enabled)
    rows = [border, _panel_line("  GET STARTED", inner, enabled=enabled, style=_WHITE)]
    for index, (command, description) in enumerate(_COMMANDS):
        rows.extend(_panel_command_lines(command, inner, enabled=enabled))
        for description_line in _wrap(description, max(1, inner - 4)):
            rows.append(
                _panel_line("    " + description_line, inner, enabled=enabled, style=_MUTED)
            )
        if index < len(_COMMANDS) - 1:
            rows.append(_panel_line("-" * inner, inner, enabled=enabled, style=_MUTED))
    rows.append(border)
    return rows


def _step_rows(*, enabled: bool, width: int | None = None) -> list[str]:
    steps = (
        ("[<>]", "ANALYZE", "Inspect the repository without execution.", _CYAN),
        ("[{}]", "REPRODUCE", "Build and test in an isolated environment.", _BLUE),
        ("[#]", "VERIFY", "Generate cryptographic evidence.", _GREEN),
    )
    rows: list[str] = []
    for index, (icon, title, description, accent) in enumerate(steps, start=1):
        rows.append(
            "  "
            + _paint(f"0{index}", _MUTED, enabled=enabled)
            + "  "
            + _paint(icon, accent, enabled=enabled)
            + "  "
            + _paint(title, accent, enabled=enabled)
        )
        description_width = max(1, (width or len(description) + 7) - 7)
        description_lines = _wrap(description, description_width)
        rows.extend("       " + _paint(line, _MUTED, enabled=enabled) for line in description_lines)
        if index < len(steps):
            rows.append("       " + _paint("|", _MUTED, enabled=enabled))
    return rows


def _footer(width: int, *, enabled: bool) -> list[str]:
    rule = _paint("-" * width, _MUTED, enabled=enabled)
    message_left = "Your repository works here."
    message_right = "Prove it can be reproduced."
    docs = "docs.reprocheck.dev"
    if width >= len(message_left) + len(message_right) + len(docs) + 8:
        gap = width - len(message_left) - 1 - len(message_right) - len(docs)
        message = (
            _paint(message_left, _WHITE, enabled=enabled)
            + " "
            + _paint(message_right, _RED, enabled=enabled)
            + " " * gap
            + _paint(docs, _MUTED, enabled=enabled)
        )
        return [rule, message]
    if width >= len(message_left) + len(message_right) + 1:
        return [
            rule,
            _paint(message_left, _WHITE, enabled=enabled)
            + " "
            + _paint(message_right, _RED, enabled=enabled),
        ]
    return [
        rule,
        *(_paint(line, _WHITE, enabled=enabled) for line in _wrap(message_left, width)),
        *(_paint(line, _RED, enabled=enabled) for line in _wrap(message_right, width)),
    ]


def _wide_layout(version: str, *, enabled: bool, terminal_width: int) -> str:
    content_width = min(148, max(108, terminal_width - 8))
    margin = max(0, (terminal_width - content_width) // 2)
    indent = " " * margin
    left_width = max(40, int(content_width * 0.40))
    gap = 4
    panel_width = content_width - left_width - gap
    left = _step_rows(enabled=enabled, width=left_width)
    right = _panel_rows(panel_width, enabled=enabled)

    lines = [
        indent + _header(version, content_width, enabled=enabled),
        "",
        *(indent + "  " + line for line in _wordmark(content_width - 2, enabled=enabled)),
        indent + "  " + _paint(_TAGLINE, _WHITE, enabled=enabled),
        "",
        *(indent + "  " + line for line in _workflow_lines(content_width - 2, enabled=enabled)),
        "",
    ]
    for index in range(max(len(left), len(right))):
        left_line = left[index] if index < len(left) else ""
        right_line = right[index] if index < len(right) else ""
        padding = " " * max(1, left_width - _visible_width(left_line))
        lines.append(indent + left_line + padding + " " * gap + right_line)
    lines.extend("" for _ in range(1))
    lines.extend(indent + line for line in _footer(content_width, enabled=enabled))
    return "\n".join(lines) + "\n"


def _compact_layout(version: str, *, enabled: bool, terminal_width: int) -> str:
    content_width = max(16, terminal_width - 4)
    indent = "  " if terminal_width >= 32 else ""
    usable = max(8, content_width - len(indent))
    lines = [indent + _header(version, usable, enabled=enabled), ""]
    lines.extend(indent + line for line in _wordmark(usable, enabled=enabled))
    lines.extend(indent + _paint(line, _MUTED, enabled=enabled) for line in _wrap(_TAGLINE, usable))
    lines.extend(("", *(indent + line for line in _workflow_lines(usable, enabled=enabled)), ""))

    for step_line in _step_rows(enabled=enabled, width=usable):
        if _visible_width(step_line) <= usable:
            lines.append(indent + step_line)
        else:
            lines.extend(indent + line for line in _wrap(_ANSI_RE.sub("", step_line), usable))
    lines.append("")
    panel_width = usable
    panel = _panel_rows(panel_width, enabled=enabled)
    lines.extend(indent + line for line in panel)
    lines.extend(indent + line for line in _footer(usable, enabled=enabled))
    return "\n".join(lines) + "\n"


def render_welcome(
    version: str,
    *,
    color: bool | None = None,
    stream: TextIO | None = None,
    width: int | None = None,
) -> str:
    """Render a deterministic, adaptive welcome without running repository code."""

    enabled = color_enabled(stream) if color is None else color
    terminal_width = max(20, width if width is not None else _terminal_width(stream))
    if terminal_width >= 112:
        return _wide_layout(version, enabled=enabled, terminal_width=terminal_width)
    return _compact_layout(version, enabled=enabled, terminal_width=terminal_width)


__all__ = ["color_enabled", "render_welcome"]
