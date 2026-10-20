import copy
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
    receipt_from_dict,
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
    def test_loader_accepts_a_valid_receipt_v1_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = build_receipt(
                Path(directory), profile="manual", verification_runs=(_run(),)
            ).to_dict()

            loaded = receipt_from_dict(payload)

            self.assertEqual(loaded.schema_version, "receipt-v1")
            self.assertTrue(validate_receipt(loaded).valid)

    def test_loader_rejects_malformed_boolean_integer_and_fingerprint_types(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = build_receipt(
                Path(directory), profile="manual", verification_runs=(_run(),)
            ).to_dict()
            cases = (
                ("repository.is_dirty", ("repository", "is_dirty"), 1),
                ("content.file_count", ("content", "file_count"), True),
                ("content.digest", ("content", "digest"), False),
                ("integrity.verified", ("integrity", "verified"), 1),
                ("integrity.method", ("integrity", "method"), 1),
                ("integrity.details", ("integrity", "details"), []),
                (
                    "integrity.content_fingerprint.file_count",
                    ("integrity", "content_fingerprint", "file_count"),
                    False,
                ),
                (
                    "command.exit_code",
                    ("verification_runs", 0, "commands", 0, "exit_code"),
                    False,
                ),
                (
                    "command.timed_out",
                    ("verification_runs", 0, "commands", 0, "timed_out"),
                    0,
                ),
            )

            for label, path, replacement in cases:
                with self.subTest(field=label):
                    payload = copy.deepcopy(base)
                    target = payload
                    for key in path[:-1]:
                        target = target[key]
                    target[path[-1]] = replacement

                    with self.assertRaises(ReceiptFormatError):
                        receipt_from_dict(payload)

    def test_loader_rejects_missing_or_invalid_integrity_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = build_receipt(
                Path(directory), profile="manual", verification_runs=(_run(),)
            ).to_dict()
            cases = (
                ("missing integrity", lambda payload: payload.pop("integrity")),
                (
                    "missing canonical hash",
                    lambda payload: payload["integrity"]["details"].pop("canonical_sha256"),
                ),
                (
                    "non-string canonical hash",
                    lambda payload: payload["integrity"]["details"].update(canonical_sha256=True),
                ),
                (
                    "malformed canonical hash",
                    lambda payload: payload["integrity"]["details"].update(
                        canonical_sha256="g" * 64
                    ),
                ),
            )

            for label, mutate in cases:
                with self.subTest(case=label):
                    payload = copy.deepcopy(base)
                    mutate(payload)
                    with self.assertRaises(ReceiptFormatError):
                        receipt_from_dict(payload)

    def test_validator_rejects_missing_or_invalid_integrity_on_models(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            receipt = build_receipt(Path(directory), profile="manual", verification_runs=(_run(),))

            missing_hash_details = dict(receipt.integrity.details)
            missing_hash_details.pop("canonical_sha256")
            missing_hash = replace(
                receipt,
                integrity=replace(receipt.integrity, details=missing_hash_details),
            )
            self.assertFalse(validate_receipt(missing_hash).valid)
            self.assertIn(
                "canonical_sha256 is missing", " ".join(validate_receipt(missing_hash).errors)
            )

            invalid_integrity = replace(
                receipt,
                integrity=replace(
                    receipt.integrity,
                    verified=1,
                    content_fingerprint=None,
                    details={"canonical_sha256": True},
                ),
            )
            validation = validate_receipt(invalid_integrity)
            self.assertFalse(validation.valid)
            self.assertIn("canonical_sha256 is invalid", " ".join(validation.errors))
            self.assertIn("integrity.verified must be a boolean", validation.errors)
            self.assertIn("integrity content fingerprint is missing", validation.errors)

    def test_loader_rejects_unsupported_receipt_schema_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = build_receipt(Path(directory), profile="manual").to_dict()

            for schema_version in ("receipt-v2", True, None):
                with self.subTest(schema_version=schema_version):
                    unsupported = copy.deepcopy(payload)
                    unsupported["schema_version"] = schema_version
                    with self.assertRaises(ReceiptFormatError):
                        receipt_from_dict(unsupported)

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
            self.assertEqual(receipt.repository.root, ".")
            self.assertNotIn(str(root), receipt.to_json())
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

    def test_experiment_details_round_trip_inside_receipt_v1(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = build_receipt(
                root,
                profile="python-uv",
                verification_runs=(_run(), _run(argument="second")),
                experiment={
                    "schema_version": "experiment-v1",
                    "classification": "drift_detected",
                    "run_count": 2,
                    "comparison": {"artifact_equal": False},
                },
            )
            receipt_path = root / "experiment-receipt.json"
            receipt_path.write_text(receipt.to_json(indent=2), encoding="utf-8")

            loaded = load_receipt(receipt_path)

            self.assertEqual(loaded.schema_version, "receipt-v1")
            self.assertEqual(
                loaded.integrity.details["experiment"]["classification"], "drift_detected"
            )
            self.assertEqual(len(loaded.verification_runs), 2)
            self.assertTrue(validate_receipt(loaded).valid)


if __name__ == "__main__":
    unittest.main()
