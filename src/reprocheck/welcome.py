"""Small, terminal-safe welcome screen for the bare ReproCheck command."""

from __future__ import annotations

import os
import sys
from typing import TextIO

_RESET = "\x1b[0m"
_OFF_WHITE = "\x1b[97m"
_GRAY = "\x1b[90m"
_CYAN = "\x1b[36m"
_GREEN = "\x1b[32m"
_RED = "\x1b[91m"

_EMBLEM = (
    "      .---------.       .---------.\n"
    "     /  .-----.  \\     /  .-----.  \\\n"
    "    |  /  R  \\  |---|  /  C  \\  |\n"
    "    |  \\_____/  |   |  \\_____/  |\n"
    "     \\         /     \\         /\n"
    "      '---.---'       '---.---'\n"
    "          \\      /\\      /\n"
    "           \\    /  \\    /\n"
    "            \\__/ [✓] \\__/\n"
)


def _paint(value: str, code: str, *, enabled: bool) -> str:
    if not enabled:
        return value
    return f"{code}{value}{_RESET}"


def color_enabled(stream: TextIO | None = None) -> bool:
    """Return whether welcome ANSI color is appropriate for the output stream."""

    output = stream if stream is not None else sys.stdout
    return bool(output.isatty()) and "NO_COLOR" not in os.environ


def render_welcome(
    version: str,
    *,
    color: bool | None = None,
    stream: TextIO | None = None,
) -> str:
    """Render a deterministic welcome without running repository code."""

    enabled = color_enabled(stream) if color is None else color
    emblem = _paint(_EMBLEM.rstrip("\n"), _CYAN, enabled=enabled)
    brand = _paint("Repro", _OFF_WHITE, enabled=enabled) + _paint("Check", _RED, enabled=enabled)

    def command(value: str) -> str:
        return _paint(f"> {value}", _GREEN, enabled=enabled)

    def command_text(value: str) -> str:
        return _paint(value, _CYAN, enabled=enabled)

    def muted(value: str) -> str:
        return _paint(value, _GRAY, enabled=enabled)

    separator = muted("  " + "-" * 72)

    lines = [
        emblem,
        "",
        f"{brand}  {muted('v' + version)}",
        muted("Repository verification. Evidence, not guesses."),
        "",
        separator,
        "",
        _paint("GET STARTED", _OFF_WHITE, enabled=enabled),
        "",
        f"{command(command_text('reprocheck scan .'))}",
        f"  {muted('inspect without execution')}",
        "",
        f"{command(command_text('reprocheck verify . --profile python-uv --backend docker'))}",
        f"  {muted('review the plan; add --approve to execute')}",
        "",
        f"{command(command_text('reprocheck receipt --help'))}",
        f"  {muted('validate or compare evidence')}",
        "",
        separator,
        "",
        f"{muted('Your repository works here.')} "
        f"{_paint('Prove it can be reproduced.', _RED, enabled=enabled)}",
        "",
    ]
    return "\n".join(lines)


__all__ = ["color_enabled", "render_welcome"]
