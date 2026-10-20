import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from reprocheck.cli import main
from reprocheck.models import (
    CommandExecution,
    CommandSpec,
    ResultState,
    VerificationClass,
    VerificationRun,
)
from reprocheck.receipts import build_receipt, compare_receipts, load_receipt, validate_receipt


def _write_receipt(root: Path, filename: str, failed: bool = False) -> Path:
    run = VerificationRun(
        run_id="python-uv-run",
        verification_class=VerificationClass.COMMAND,
        state=ResultState.VERIFIED_FAIL if failed else ResultState.VERIFIED_PASS,
        commands=(
            CommandExecution(
                command=CommandSpec("python", ("-m", "pytest")),
                exit_code=1 if failed else 0,
                result_state=ResultState.VERIFIED_FAIL if failed else ResultState.VERIFIED_PASS,
                stdout="fixture output",
            ),
        ),
    )
    receipt = build_receipt(root, profile="python-uv", verification_runs=(run,))
    path = root / filename
    path.write_text(receipt.to_json(), encoding="utf-8")
    return path


def _cli(*arguments: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(list(arguments))
    return code, stdout.getvalue(), stderr.getvalue()


class ReceiptCliRegressionTests(unittest.TestCase):
    def test_failed_execution_is_integrity_valid_not_a_passing_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = _write_receipt(root, "failed.json", failed=True)
            self.assertEqual(_cli("receipt", "validate", str(path)), (0, "valid\n", ""))
            receipt = load_receipt(path)
            self.assertTrue(validate_receipt(receipt).valid)
            self.assertEqual(receipt.verification_runs[0].state, ResultState.VERIFIED_FAIL)

    def test_passing_and_failed_outcomes_differ_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            passing = _write_receipt(root, "passed.json")
            # Keep source fingerprints identical without including the first output receipt.
            passed_receipt = load_receipt(passing)
            passing.unlink()
            failing = _write_receipt(root, "failed.json", failed=True)
            failed_receipt = load_receipt(failing)
            passing.write_text(passed_receipt.to_json(), encoding="utf-8")
            comparison = compare_receipts(passed_receipt, failed_receipt)
            self.assertEqual(comparison.changed, ("outcome",))
            result = _cli("receipt", "compare", str(passing), str(failing))
            self.assertEqual(result, (1, "changed\n- outcome\n", ""))
            self.assertEqual(result, _cli("receipt", "compare", str(failing), str(passing)))

    def test_missing_malformed_and_unsupported_schema_are_input_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(_cli("receipt", "validate", str(root / "missing.json"))[0], 2)
            path = root / "receipt.json"
            path.write_text("not JSON", encoding="utf-8")
            self.assertEqual(_cli("receipt", "validate", str(path))[0], 2)
            path = _write_receipt(root, "receipt.json")
            payload = json.loads(path.read_text())
            payload["schema_version"] = "receipt-v99"
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(_cli("receipt", "validate", str(path))[0], 2)

    def test_tampered_evidence_and_hash_are_rejected_without_printing_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = _write_receipt(root, "receipt.json")
            original = json.loads(path.read_text())
            for field in ("command", "hash"):
                with self.subTest(field=field):
                    payload = json.loads(json.dumps(original))
                    if field == "command":
                        payload["verification_runs"][0]["commands"][0]["stdout"] = "private payload"
                    else:
                        payload["integrity"]["details"]["canonical_sha256"] = "0" * 64
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    code, stdout, stderr = _cli("receipt", "validate", str(path))
                    self.assertEqual(code, 1)
                    self.assertEqual(stdout, "invalid\n")
                    self.assertIn("does not match", stderr)
                    self.assertNotIn("private payload", stdout + stderr)

    def test_compare_rejects_bad_integrity_instead_of_reporting_equal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = _write_receipt(root, "receipt.json")
            payload = json.loads(path.read_text())
            payload["integrity"]["details"]["canonical_sha256"] = "0" * 64
            path.write_text(json.dumps(payload), encoding="utf-8")
            code, stdout, stderr = _cli("receipt", "compare", str(path), str(path))
            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertIn("integrity", stderr)


if __name__ == "__main__":
    unittest.main()
