import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from reprocheck.models import (
    Assessment,
    CheckCategory,
    CommandExecution,
    CommandSpec,
    EnvironmentFingerprint,
    Finding,
    ResultState,
    Severity,
    VerificationClass,
    VerificationRun,
)
from reprocheck.receipts import (
    ReceiptFormatError,
    build_receipt,
    canonical_hash,
    compare_receipts,
    load_receipt,
    validate_receipt,
)


def _finding(message: str = "The test result changed") -> Finding:
    return Finding(
        check_id="RC-TEST-001",
        title="Test result",
        message=message,
        assessment=Assessment.FAIL,
        severity=Severity.MEDIUM,
        category=CheckCategory.TESTS,
        state=ResultState.VERIFIED_FAIL,
    )


def _run(
    *, argument: str = "ok", exit_code: int = 0, environment_value: str = "one"
) -> VerificationRun:
    command = CommandExecution(
        command=CommandSpec(executable="python", args=("-c", argument), cwd="."),
        result_state=(ResultState.VERIFIED_PASS if exit_code == 0 else ResultState.VERIFIED_FAIL),
        exit_code=exit_code,
        stdout="bounded output",
        started_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T00:00:01Z",
    )
    environment = EnvironmentFingerprint(
        os_name="Linux",
        architecture="x86_64",
        python_version=environment_value,
        runtime_versions={"python": environment_value},
    )
    return VerificationRun(
        run_id="test-run",
        verification_class=VerificationClass.TEST,
        state=command.result_state,
        commands=(command,),
        environment=environment,
    )


class ReceiptTests(unittest.TestCase):
    def test_receipt_contains_profile_findings_limitations_and_valid_canonical_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.py").write_text("print('ok')\n", encoding="utf-8")
            receipt = build_receipt(
                root,
                profile="python-uv",
                verification_runs=(_run(),),
                findings=(_finding(),),
                limitations=("Host execution is not a complete sandbox.",),
                generated_at="2026-01-01T00:00:00Z",
            )

            self.assertEqual(receipt.integrity.details["profile"], "python-uv")
            self.assertEqual(receipt.integrity.details["canonical_sha256"], canonical_hash(receipt))
            self.assertEqual(len(receipt.evidence), 1)
            self.assertIn("finding", receipt.evidence[0].details)
            validation = validate_receipt(receipt, repository=root)
            self.assertTrue(validation)
            self.assertEqual(validation.content_matches, True)

    def test_validator_detects_tampering_and_comparison_ignores_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.py").write_text("print('ok')\n", encoding="utf-8")
            receipt = build_receipt(
                root,
                profile="manual",
                verification_runs=(_run(),),
                generated_at="2026-01-01T00:00:00Z",
            )
            changed_time_run = replace(
                receipt.verification_runs[0],
                started_at="2030-01-01T00:00:00Z",
                finished_at="2030-01-01T00:00:01Z",
                commands=(
                    replace(
                        receipt.verification_runs[0].commands[0],
                        started_at="2030-01-01T00:00:00Z",
                        finished_at="2030-01-01T00:00:01Z",
                    ),
                ),
            )
            timestamp_only = replace(
                receipt,
                generated_at="2030-01-01T00:00:00Z",
                verification_runs=(changed_time_run,),
            )
            self.assertTrue(compare_receipts(receipt, timestamp_only).equal)

            tampered_details = dict(receipt.integrity.details)
            tampered_details["canonical_sha256"] = "0" * 64
            tampered = replace(
                receipt,
                integrity=replace(receipt.integrity, details=tampered_details),
            )
            self.assertFalse(validate_receipt(tampered).valid)

    def test_receipt_builder_bounds_and_redacts_direct_command_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            noisy_command = replace(
                _run().commands[0],
                stdout="api_key=do-not-store\n" + ("x" * 100_000),
                stderr="password=also-do-not-store",
            )
            receipt = build_receipt(
                root,
                profile="manual",
                commands=(noisy_command,),
            )

            observed = receipt.verification_runs[0].commands[0]
            self.assertIn("<REDACTED_VALUE>", observed.stdout)
            self.assertIn("<REDACTED_VALUE>", observed.stderr)
            self.assertLessEqual(len(observed.stdout), 32 * 1024)

    def test_comparison_reports_each_meaningful_change_category(self) -> None:
        with (
            tempfile.TemporaryDirectory() as first_directory,
            tempfile.TemporaryDirectory() as second_directory,
        ):
            first_root = Path(first_directory)
            second_root = Path(second_directory)
            (first_root / "source.py").write_text("one", encoding="utf-8")
            (second_root / "source.py").write_text("two", encoding="utf-8")
            first = build_receipt(
                first_root,
                profile="manual",
                verification_runs=(_run(),),
                findings=(_finding(),),
            )
            second = build_receipt(
                second_root,
                profile="manual",
                verification_runs=(_run(argument="changed", exit_code=1, environment_value="two"),),
                findings=(_finding("A different finding"),),
            )

            comparison = compare_receipts(first, second)
            self.assertEqual(
                comparison.changed,
                ("content", "command", "finding", "outcome", "environment"),
            )

    def test_receipt_json_round_trips_through_the_loader(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = build_receipt(root, profile="manual", verification_runs=(_run(),))
            receipt_path = root / "receipt.json"
            receipt_path.write_text(receipt.to_json(indent=2), encoding="utf-8")

            loaded = load_receipt(receipt_path)

            self.assertEqual(loaded.receipt_id, receipt.receipt_id)
            self.assertEqual(validate_receipt(loaded).valid, True)

            receipt_path.write_text("{}", encoding="utf-8")
            with self.assertRaises(ReceiptFormatError):
                load_receipt(receipt_path)


if __name__ == "__main__":
    unittest.main()
