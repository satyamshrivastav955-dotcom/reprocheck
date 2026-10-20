import io
import re
import unittest
from unittest.mock import patch

from reprocheck.welcome import color_enabled, render_welcome

ANSI = re.compile(r"\x1b\[[0-9;]*m")
WIDTHS = (20, 40, 80, 100, 111, 112, 120, 160, 200)


def _normalized(rendered: str) -> str:
    lines = (ANSI.sub("", line).strip() for line in rendered.splitlines())
    return " ".join(
        line.strip("|").strip().removeprefix("> ")
        for line in lines
        if line and not line.startswith("+")
    )


class _TTYBuffer(io.StringIO):
    def __init__(self, columns: int = 120) -> None:
        super().__init__()
        self.columns = columns

    def isatty(self) -> bool:
        return True


class WelcomeTests(unittest.TestCase):
    def test_rendering_stays_within_requested_widths(self) -> None:
        commands = (
            "reprocheck scan .",
            "reprocheck verify . --profile python-uv --backend docker",
            "reprocheck experiment . --profile python-uv --backend docker "
            "--artifact PATH --approve --receipt FILE",
            "reprocheck receipt validate FILE",
            "reprocheck receipt compare FIRST SECOND",
            "reprocheck --help",
        )
        for width in WIDTHS:
            with self.subTest(width=width):
                plain = render_welcome("9.9.9", color=False, width=width)
                colored = render_welcome("9.9.9", color=True, width=width)
                self.assertEqual(ANSI.sub("", colored), plain)
                self.assertTrue(plain.isascii())
                self.assertLessEqual(max(map(len, plain.splitlines())), width)
                text = _normalized(plain)
                for command in commands:
                    self.assertIn(command, text)
                if width >= 40:
                    self.assertIn("v9.9.9", plain)
                else:
                    self.assertIn("REPROCHECK", plain)

    def test_only_primary_action_has_a_panel_with_consistent_ascii_boundaries(self) -> None:
        for width in WIDTHS:
            with self.subTest(width=width):
                lines = render_welcome("0.1.0", color=False, width=width).splitlines()
                borders = [
                    index for index, line in enumerate(lines) if line.strip().startswith("+")
                ]
                self.assertEqual(len(borders), 2)
                panel_lines = lines[borders[0] : borders[1] + 1]
                self.assertEqual(len({len(line) for line in panel_lines}), 1)
                self.assertTrue(
                    all(line.rstrip().endswith("+") for line in (panel_lines[0], panel_lines[-1]))
                )
                self.assertTrue(
                    all(
                        line.strip().startswith("|") and line.endswith("|")
                        for line in panel_lines[1:-1]
                    )
                )
                self.assertIn("START HERE", _normalized("\n".join(panel_lines)))
                self.assertIn("reprocheck scan .", _normalized("\n".join(panel_lines)))

    def test_primary_action_and_core_message_appear_early_before_reference(self) -> None:
        for width in WIDTHS:
            with self.subTest(width=width):
                rendered = render_welcome("0.1.0", color=False, width=width)
                text = _normalized(rendered)
                self.assertEqual(text.count("START HERE"), 1)
                self.assertEqual(text.count("reprocheck scan ."), 1)
                self.assertEqual(rendered.count("> "), 1)
                self.assertLess(
                    text.index("Passing tests does not prove reproducibility."),
                    text.index("START HERE"),
                )
                self.assertLess(text.index("records the evidence."), text.index("START HERE"))
                self.assertLess(text.index("reprocheck scan ."), text.index("WORKFLOW REFERENCE"))
                prompt_line = next(
                    index for index, line in enumerate(rendered.splitlines()) if "> " in line
                )
                if width >= 80:
                    self.assertLess(prompt_line, 18)
                self.assertIn("Read-only: inspect the repository without executing it.", text)

    def test_narrow_layout_wraps_long_content_without_overflow(self) -> None:
        rendered = render_welcome("0.1.0", color=False, width=40)
        lines = rendered.splitlines()
        self.assertLessEqual(max(map(len, lines)), 40)
        self.assertIn("ReproCheck", rendered)
        self.assertIn(
            "reprocheck verify . --profile python-uv --backend docker", _normalized(rendered)
        )
        self.assertLess(rendered.index("START HERE"), rendered.index("WORKFLOW REFERENCE"))

    def test_workflow_explains_approval_profiles_and_inspectable_scoped_evidence(self) -> None:
        text = _normalized(render_welcome("0.1.0", color=False, width=100))
        for message in (
            "ReproCheck runs controlled experiments in isolated environments, "
            "compares selected artifacts, and records the evidence.",
            "Results apply to recorded conditions only.",
            "Receipt integrity is not producer trust.",
            "Python/uv example; choose a supported profile for your repository.",
            "Docker is required for these commands; no silent host fallback.",
            "Review the plan without execution; rerun with --approve only after review: "
            "reprocheck verify . --profile python-uv --backend docker",
            "Compare two independent runs and selected files/directories (--artifact).",
            "Preview without --approve, then explicitly approve both runs:",
            "Validate receipt integrity; open FILE for JSON run outcomes and artifact hashes:",
        ):
            self.assertIn(message, text)
        for obsolete in ("cryptographic", "Prove it", "Evidence, not guesses", "SCAN | ISOLATE"):
            self.assertNotIn(obsolete, text)

    def test_color_is_stream_aware_and_no_color_wins(self) -> None:
        tty = _TTYBuffer()
        with patch.dict("os.environ", {"TERM": "xterm-256color"}, clear=True):
            self.assertTrue(color_enabled(tty))
            self.assertIn("\x1b[", render_welcome("0.1.0", stream=tty))
            redirected = io.StringIO()
            self.assertFalse(color_enabled(redirected))
            self.assertNotIn("\x1b[", render_welcome("0.1.0", stream=redirected))
            for environment in ({"NO_COLOR": "1"}, {"NO_COLOR": ""}, {"TERM": "dumb"}):
                with self.subTest(environment=environment), patch.dict("os.environ", environment):
                    self.assertFalse(color_enabled(tty))
                    plain = render_welcome("0.1.0", stream=tty)
                    self.assertNotIn("\x1b[", plain)
                    self.assertTrue(plain.isascii())

        self.assertNotIn("\x1b[", render_welcome("0.1.0", color=False, width=120))

    def test_stream_width_and_explicit_override_are_deterministic(self) -> None:
        tty = _TTYBuffer(columns=40)
        self.assertEqual(
            render_welcome("0.1.0", color=False, stream=tty),
            render_welcome("0.1.0", color=False, width=40),
        )
        self.assertEqual(
            render_welcome("0.1.0", color=False, stream=tty, width=120),
            render_welcome("0.1.0", color=False, width=120),
        )

    def test_footer_points_to_drift_demo_and_explains_expected_outcome(self) -> None:
        text = _normalized(render_welcome("0.1.0", color=False, width=100))
        self.assertIn("Drift demo: docs/reproducibility-drift-demo.md", text)
        self.assertIn("Expected: both runs pass; selected artifacts differ: drift_detected.", text)
        self.assertIn("Reproducibility not established; inspect the receipt for evidence.", text)


if __name__ == "__main__":
    unittest.main()
