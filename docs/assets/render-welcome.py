"""Capture the genuine CLI in a PTY and export its opening as a static SVG.

Run from any directory with Python 3.12 on POSIX. Uses only the standard library;
no application renderer is replaced. The full ANSI capture stays in memory.
"""

from __future__ import annotations

import fcntl
import html
import os
import pty
import re
import struct
import subprocess
import termios
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESTINATION = Path(__file__).with_name("reprocheck-cli-welcome.svg")
ANSI = re.compile(r"\x1b\[([0-9;]*)m")
COLORS = {
    90: "#858585",
    91: "#ff5555",
    92: "#55ff55",
    94: "#5555ff",
    96: "#55ffff",
    97: "#ffffff",
}


def main() -> None:
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 60, 86, 0, 0))
    environment = {**os.environ, "TERM": "xterm-256color", "COLUMNS": "86"}
    environment.pop("NO_COLOR", None)
    process = subprocess.Popen(
        ["uv", "run", "--locked", "--offline", "reprocheck"],
        cwd=ROOT,
        env=environment,
        stdout=slave,
        stderr=slave,
    )
    os.close(slave)
    chunks = []
    try:
        while True:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(master)
    assert process.wait() == 0, "CLI capture failed"
    output = b"".join(chunks).decode("utf-8").replace("\r\n", "\n")
    # A contiguous opening crop: identity, purpose and first-command panel.
    rows = output.splitlines()
    end = next(i for i, row in enumerate(rows) if "WORKFLOW REFERENCE" in row)
    rows = rows[:end]
    while rows and not ANSI.sub("", rows[-1]).strip():
        rows.pop()
    width, line_height, margin, cell = 856, 23, 24, 9.6333
    height = margin * 2 + len(rows) * line_height
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        '<title id="title">ReproCheck CLI welcome — actual terminal output</title>',
        '<desc id="desc">Faithful SVG rendering of the genuine 86-column CLI welcome, '
        "cropped to its identity, purpose and first-command panel. "
        "Passing tests does not prove reproducibility. "
        "Start here: reprocheck scan . — read-only repository inspection.</desc>",
        f'<rect width="{width}" height="{height}" rx="10" fill="#111110"/>',
        '<g font-family="DejaVu Sans Mono, Consolas, monospace" font-size="16" '
        'xml:space="preserve">',
    ]
    for row_index, row in enumerate(rows):
        column, color, bold, position = 0, "#ffffff", False, 0
        for match in [*ANSI.finditer(row), None]:
            end_position = match.start() if match else len(row)
            text = row[position:end_position]
            if text:
                svg.append(
                    f'<text x="{margin + column * cell:.3f}" '
                    f'y="{margin + 16 + row_index * line_height}" fill="{color}" '
                    f'font-weight="{700 if bold else 400}">{html.escape(text)}</text>'
                )
                column += len(text)
            if match is not None:
                for code in (int(value or 0) for value in match[1].split(";")):
                    if code == 0:
                        color, bold = "#ffffff", False
                    elif code == 1:
                        bold = True
                    elif code in COLORS:
                        color = COLORS[code]
                position = match.end()
    svg.extend(["</g>", "</svg>"])
    DESTINATION.write_text("\n".join(svg) + "\n", encoding="utf-8")
    print(f"Captured {len(rows)} real CLI rows → {DESTINATION.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
