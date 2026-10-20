import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from reprocheck.cli import main, scan_repository
from reprocheck.reporting import render_json


class CliTests(unittest.TestCase):
    def test_bare_invocation_shows_branded_welcome(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        self.assertIn("ReproCheck", output.getvalue())
        self.assertIn("GET STARTED", output.getvalue())
        self.assertIn("reprocheck scan .", output.getvalue())
        self.assertIn("reprocheck verify . --profile python-uv --backend docker", output.getvalue())
        self.assertIn("reprocheck receipt --help", output.getvalue())
        self.assertEqual(errors.getvalue(), "")
        self.assertNotIn("usage:", output.getvalue().lower())

    def test_help_and_version_do_not_include_welcome(self) -> None:
        for arguments in (
            ["--help"],
            ["scan", "--help"],
            ["verify", "--help"],
            ["receipt", "--help"],
        ):
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                main(arguments)
            self.assertNotIn("GET STARTED", output.getvalue())

        version = io.StringIO()
        with redirect_stdout(version):
            main(["--version"])
        self.assertEqual(version.getvalue(), "reprocheck 0.1.0\n")
        self.assertNotIn("GET STARTED", version.getvalue())

    def test_no_color_disables_welcome_ansi_for_a_tty(self) -> None:
        class TTYBuffer(io.StringIO):
            def isatty(self) -> bool:
                return True

        output = TTYBuffer()
        with patch.dict("os.environ", {"NO_COLOR": "1"}), redirect_stdout(output):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        self.assertNotIn("\x1b[", output.getvalue())

    def test_scan_builds_static_report_and_threshold_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["scan", str(root), "--format", "json", "--fail-on", "critical"])

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["schema_version"], "report-v1")
            self.assertEqual(payload["repository"]["root"], str(root.resolve()))
            self.assertIsNone(payload["evidence_receipt"])
            self.assertEqual(payload["verification_runs"], [])
            self.assertIn("content_fingerprint", payload["summary"])
            self.assertIn("score_summary", payload["summary"])
            self.assertIsNone(payload["summary"]["score_summary"]["verified_score"])
            self.assertNotIn("verified_pass", output.getvalue())
            self.assertNotIn("GET STARTED", output.getvalue())

            medium_output = io.StringIO()
            with redirect_stdout(medium_output):
                medium_exit = main(["scan", str(root), "--format", "json", "--fail-on", "medium"])
            self.assertEqual(medium_exit, 1)

    def test_scan_output_is_deterministic_and_can_be_written_to_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "README.md").write_text("Run `python -m pytest`.\n")
            first = scan_repository(root).to_json(indent=2)
            second = scan_repository(root).to_json(indent=2)
            self.assertEqual(first, second)

            with tempfile.TemporaryDirectory() as output_directory:
                output_file = Path(output_directory) / "report.json"
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    exit_code = main(
                        [
                            "scan",
                            str(root),
                            "--format",
                            "json",
                            "--output",
                            str(output_file),
                            "--fail-on",
                            "critical",
                        ]
                    )
                self.assertEqual(exit_code, 0)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(
                    json.loads(output_file.read_text()),
                    json.loads(render_json(scan_repository(root))),
                )

    def test_invalid_path_is_a_usage_error_and_version_is_available(self) -> None:
        errors = io.StringIO()
        with redirect_stderr(errors):
            exit_code = main(["scan", "/path/that/does/not/exist"])
        self.assertEqual(exit_code, 2)
        self.assertIn("input error", errors.getvalue())

        version = io.StringIO()
        with redirect_stdout(version):
            version_exit = main(["--version"])
        self.assertEqual(version_exit, 0)
        self.assertEqual(version.getvalue(), "reprocheck 0.1.0\n")

    def test_verify_requires_approval_and_emits_a_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "pyproject.toml").write_text(
                "[project]\nname='fixture'\nversion='0.1.0'\n", encoding="utf-8"
            )
            (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
            receipt_path = root / "receipt.json"
            output = io.StringIO()
            plan_output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(plan_output):
                exit_code = main(
                    [
                        "verify",
                        str(root),
                        "--backend",
                        "docker",
                        "--format",
                        "json",
                        "--receipt",
                        str(receipt_path),
                        "--fail-on",
                        "critical",
                    ]
                )
            self.assertEqual(exit_code, 1)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["summary"]["verification_status"], "denied")
            self.assertEqual(payload["verification_runs"][0]["state"], "not_verified")
            self.assertEqual(payload["summary"]["score_summary"]["verified_score"], 0.0)
            self.assertEqual(
                payload["summary"]["score_summary"]["verified_denominator"],
                payload["summary"]["score_summary"]["denominator"],
            )
            self.assertTrue(receipt_path.exists())
            self.assertIn("approval: required", plan_output.getvalue())
            self.assertIn("uv sync --locked", plan_output.getvalue())


if __name__ == "__main__":
    unittest.main()
