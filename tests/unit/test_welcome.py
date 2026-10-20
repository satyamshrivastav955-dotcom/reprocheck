import io
import re
import unittest
from unittest.mock import patch

from reprocheck.welcome import color_enabled, render_welcome

ANSI = re.compile(r"\x1b\[[0-9;]*m")


class _TTYBuffer(io.StringIO):
    def __init__(self, columns: int = 120) -> None:
        super().__init__()
        self.columns = columns

    def isatty(self) -> bool:
        return True


class WelcomeTests(unittest.TestCase):
    def test_rendering_stays_within_requested_widths(self) -> None:
        for width in (80, 100, 120, 160, 200):
            rendered = render_welcome("9.9.9", color=True, width=width)
            visible_lines = [ANSI.sub("", line) for line in rendered.splitlines()]
            visible = "\n".join(visible_lines)
            self.assertLessEqual(max(map(len, visible_lines)), width)
            self.assertIn("reprocheck scan .", visible)
            self.assertIn("reprocheck verify", visible)
            self.assertIn("reprocheck receipt --help", visible)
            self.assertIn("v9.9.9", visible)

    def test_wide_panel_has_consistent_boundaries(self) -> None:
        lines = render_welcome("0.1.0", color=False, width=120).splitlines()
        header_index = next(index for index, line in enumerate(lines) if "GET STARTED" in line)
        panel_start = header_index - 1
        panel_end = next(
            index
            for index in range(header_index, len(lines))
            if lines[index].strip().startswith("+")
        )
        panel_lines = lines[panel_start : panel_end + 1]
        self.assertGreaterEqual(len(panel_lines), 8)
        self.assertEqual(len({len(line) for line in panel_lines}), 1)
        self.assertTrue(panel_lines[0].rstrip().endswith("+"))
        self.assertTrue(panel_lines[-1].rstrip().endswith("+"))

    def test_narrow_layout_wraps_long_content_without_overflow(self) -> None:
        rendered = render_welcome("0.1.0", color=False, width=40)
        lines = rendered.splitlines()
        self.assertLessEqual(max(map(len, lines)), 40)
        self.assertIn("reprocheck verify", rendered)
        self.assertIn("python-uv", rendered)
        self.assertIn("docker", rendered)
        self.assertGreater(rendered.index("GET STARTED"), rendered.index("ANALYZE"))

    def test_color_is_stream_aware_and_no_color_wins(self) -> None:
        tty = _TTYBuffer()
        self.assertTrue(color_enabled(tty))
        self.assertIn("\x1b[", render_welcome("0.1.0", stream=tty))

        with patch.dict("os.environ", {"NO_COLOR": "1"}):
            self.assertFalse(color_enabled(tty))
            self.assertNotIn("\x1b[", render_welcome("0.1.0", stream=tty))

        with patch.dict("os.environ", {"TERM": "dumb"}, clear=False):
            self.assertFalse(color_enabled(tty))
            self.assertNotIn("\x1b[", render_welcome("0.1.0", stream=tty))

        self.assertNotIn("\x1b[", render_welcome("0.1.0", color=False, width=120))

    def test_footer_docs_link_is_reserved_for_roomy_layouts(self) -> None:
        self.assertIn("docs.reprocheck.dev", render_welcome("0.1.0", color=False, width=100))
        self.assertNotIn("docs.reprocheck.dev", render_welcome("0.1.0", color=False, width=40))


if __name__ == "__main__":
    unittest.main()
