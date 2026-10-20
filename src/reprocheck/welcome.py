"""Terminal-safe, adaptive welcome screen for the bare ReproCheck command."""

from __future__ import annotations

import os
import shutil
import sys
from typing import Protocol, TextIO, cast

_RESET = "\x1b[0m"
_OFF_WHITE = "\x1b[97m"
_GRAY = "\x1b[90m"
_CYAN = "\x1b[36m"
_GREEN = "\x1b[32m"
_RED = "\x1b[91m"

# Two independent paths meet at a checked evidence receipt.  The art is kept
# deliberately ASCII-only so it survives PowerShell, cmd.exe, and plain pipes.
_EMBLEM_LINES = (
    "        .-----------------.       .-----------------.",
    "       /   REPOSITORY       \\     /   CLEAN ROOM      " + "/",
    "      /  .-------------.     \\   /  .-------------.    " + "/",
    "     /  /  manifest + pin \\     \\ /  /  command path  \\    " + "/",
    "    |  |  lock + source  |-----X----|  build + test   |    |",
    "     \\  \\_____________/     / \\    \\_____________/    /",
    "      \\____________________/   \\____________________/",
    "                  \\           /",
    "                   \\         /",
    "                    \\   ^   /",
    "                     \\ [OK]/",
    "                      \\___/",
    "                 .-----|-----.",
    "                /   EVIDENCE   " + "\\",
    "               '----------------'",
)


def _paint(value: str, code: str, *, enabled: bool) -> str:
    if not enabled:
        return value
    return f"{code}{value}{_RESET}"


def color_enabled(stream: TextIO | None = None) -> bool:
    """Return whether welcome ANSI color is appropriate for the output stream."""

    output = stream if stream is not None else sys.stdout
    return bool(output.isatty()) and "NO_COLOR" not in os.environ


def _right_lines(version: str) -> tuple[tuple[str, str], ...]:
    return (
        ("blank", ""),
        ("brand", "ReproCheck"),
        ("muted", "Repository verification. Evidence, not guesses."),
        ("muted", f"v{version}"),
        ("separator", "-" * 56),
        ("blank", ""),
        ("label", "GET STARTED"),
        ("command", "reprocheck scan ."),
        ("muted", "  inspect without execution"),
        ("command", "reprocheck verify . --profile python-uv --backend docker"),
        ("muted", "  review the plan; add --approve to execute"),
        ("command", "reprocheck receipt --help"),
        ("muted", "  validate and compare evidence"),
        ("separator", "-" * 56),
        ("tagline", "Your repository works here. Prove it can be reproduced."),
    )


def _render_right_line(kind: str, value: str, *, enabled: bool) -> str:
    if kind == "brand":
        return _paint("Repro", _OFF_WHITE, enabled=enabled) + _paint("Check", _RED, enabled=enabled)
    if kind == "command":
        command, _, arguments = value.partition(" ")
        return _paint("> " + command, _GREEN, enabled=enabled) + (
            " " + _paint(arguments, _CYAN, enabled=enabled) if arguments else ""
        )
    if kind == "label":
        return _paint(value, _OFF_WHITE, enabled=enabled)
    if kind == "tagline":
        prefix, _, emphasis = value.partition(" Prove")
        return _paint(prefix, _GRAY, enabled=enabled) + _paint(
            " Prove" + emphasis, _RED, enabled=enabled
        )
    if kind in {"muted", "separator"}:
        return _paint(value, _GRAY, enabled=enabled)
    return value


class _ColumnStream(Protocol):
    columns: int


def _terminal_width(stream: TextIO | None) -> int:
    if stream is not None and hasattr(stream, "columns"):
        columns = cast(_ColumnStream, stream).columns
        if isinstance(columns, int) and columns > 0:
            return columns
    return shutil.get_terminal_size(fallback=(100, 24)).columns


def render_welcome(
    version: str,
    *,
    color: bool | None = None,
    stream: TextIO | None = None,
    width: int | None = None,
) -> str:
    """Render a deterministic adaptive welcome without running repository code."""

    enabled = color_enabled(stream) if color is None else color
    right = _right_lines(version)
    right_plain = tuple(value for _, value in right)
    left_width = max(len(line) for line in _EMBLEM_LINES)
    right_width = max(len(line) for line in right_plain)
    available_width = width if width is not None else _terminal_width(stream)
    side_by_side = available_width >= left_width + right_width + 5

    emblem = _paint("\n".join(_EMBLEM_LINES), _CYAN, enabled=enabled)
    if side_by_side:
        rendered_right = tuple(
            _render_right_line(kind, value, enabled=enabled) for kind, value in right
        )
        lines: list[str] = []
        for index in range(max(len(_EMBLEM_LINES), len(rendered_right))):
            left = _EMBLEM_LINES[index] if index < len(_EMBLEM_LINES) else ""
            right_line = rendered_right[index] if index < len(rendered_right) else ""
            lines.append(
                _paint(left, _CYAN, enabled=enabled)
                + " " * (left_width - len(left) + 5)
                + right_line
            )
        return "\n".join(lines) + "\n"

    separator = _paint("-" * min(72, max(left_width, right_width)), _GRAY, enabled=enabled)
    rendered_right = tuple(
        _render_right_line(kind, value, enabled=enabled) for kind, value in right
    )
    return "\n".join((emblem, "", separator, "", *rendered_right[1:], ""))


__all__ = ["color_enabled", "render_welcome"]
