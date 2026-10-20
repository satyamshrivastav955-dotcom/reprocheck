"""A restrained, terminal-safe welcome screen for the bare ReproCheck command.

The renderer deliberately uses ASCII geometry only.  That keeps the lockup
readable in PowerShell, cmd.exe, POSIX terminals, redirected output, and
terminals with incomplete Unicode fonts.  ANSI color is an optional layer on
top of the same deterministic plain-text layout.
"""

from __future__ import annotations

import os
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
_BLUE = "\x1b[94m"

_MESSAGE = "Passing tests does not prove reproducibility."
_PURPOSE = (
    "ReproCheck runs controlled experiments in isolated environments, compares selected "
    "artifacts, and records the evidence."
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
    rows = [border, _panel_line("  START HERE", inner, enabled=enabled, style=_GREEN)]
    rows.extend(_panel_command_lines("reprocheck scan .", inner, enabled=enabled))
    for line in _wrap("Read-only: inspect the repository without executing it.", max(1, inner - 4)):
        rows.append(_panel_line("    " + line, inner, enabled=enabled, style=_MUTED))
    rows.append(border)
    return rows


def _workflow_rows(width: int, *, enabled: bool) -> list[str]:
    rows = [_paint(line, _BLUE, enabled=enabled) for line in _wrap("WORKFLOW REFERENCE", width)]
    notes = (
        "Python/uv example; choose a supported profile for your repository.",
        "Docker is required for these commands; no silent host fallback.",
    )
    for note in notes:
        rows.extend(_paint(line, _MUTED, enabled=enabled) for line in _wrap(note, width))
    commands = (
        (
            "Review the plan without execution; rerun with --approve only after review:",
            "reprocheck verify . --profile python-uv --backend docker",
        ),
        (
            "Compare two independent runs and selected files/directories (--artifact). "
            "Preview without --approve, then explicitly approve both runs:",
            "reprocheck experiment . --profile python-uv --backend docker "
            "--artifact PATH --approve --receipt FILE",
        ),
        (
            "Validate receipt integrity; open FILE for JSON run outcomes and artifact hashes:",
            "reprocheck receipt validate FILE",
        ),
    )
    for description, command in commands:
        rows.append("")
        rows.extend(_paint(line, _MUTED, enabled=enabled) for line in _wrap(description, width))
        rows.extend(
            "  " + _style_command(line, enabled=enabled) for line in _wrap(command, width - 2)
        )
    return rows


def _footer(width: int, *, enabled: bool) -> list[str]:
    notes = (
        "Advanced: reprocheck --help; reprocheck receipt compare FIRST SECOND",
        "Drift demo: docs/reproducibility-drift-demo.md",
        "Expected: both runs pass; selected artifacts differ: drift_detected. "
        "Reproducibility not established; inspect the receipt for evidence.",
        "Results apply to recorded conditions only. Receipt integrity is not producer trust.",
    )
    return [_paint(line, _MUTED, enabled=enabled) for note in notes for line in _wrap(note, width)]


def _content_rows(version: str, width: int, *, enabled: bool) -> list[str]:
    rows = [_header(version, width, enabled=enabled), ""]
    rows.extend(_wordmark(width, enabled=enabled))
    rows.extend(_paint(line, _WHITE, enabled=enabled) for line in _wrap(_MESSAGE, width))
    rows.extend(_paint(line, _MUTED, enabled=enabled) for line in _wrap(_PURPOSE, width))
    rows.append("")
    rows.extend(_panel_rows(width, enabled=enabled))
    rows.append("")
    rows.extend(_workflow_rows(width, enabled=enabled))
    rows.append("")
    rows.extend(_footer(width, enabled=enabled))
    return rows


def _wide_layout(version: str, *, enabled: bool, terminal_width: int) -> str:
    content_width = min(148, max(108, terminal_width - 8))
    margin = max(0, (terminal_width - content_width) // 2)
    indent = " " * margin
    rows = _content_rows(version, content_width, enabled=enabled)
    return "\n".join(indent + row if row else "" for row in rows) + "\n"


def _compact_layout(version: str, *, enabled: bool, terminal_width: int) -> str:
    content_width = max(16, terminal_width - 4)
    indent = "  " if terminal_width >= 32 else ""
    usable = max(8, content_width - len(indent))
    rows = _content_rows(version, usable, enabled=enabled)
    return "\n".join(indent + row if row else "" for row in rows) + "\n"


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
