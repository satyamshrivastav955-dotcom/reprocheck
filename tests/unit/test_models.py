import json
import unittest
from pathlib import Path

from reprocheck.models import (
    Assessment,
    CheckCategory,
    CommandExecution,
    CommandSpec,
    ContentFingerprint,
    Evidence,
    EvidenceReceipt,
    Finding,
    IntegrityMetadata,
    Report,
    RepositoryIdentity,
    ResultState,
    Severity,
    VerificationClass,
    dumps_json,
    finding_fingerprint,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ModelSerializationTests(unittest.TestCase):
    def test_nested_models_and_enums_serialize_to_json_values(self) -> None:
        command = CommandSpec(
            executable="python",
            args=("-m", "pytest"),
            env={"MODE": "check"},
        )
        execution = CommandExecution(command=command, exit_code=0)
        evidence = Evidence(
            id="evidence-1",
            verification_class=VerificationClass.TEST,
            summary="Tests completed",
            command=execution,
        )
        finding = Finding(
            check_id="tests.run",
            title="Tests pass",
            message="The test suite completed successfully.",
            assessment=Assessment.PASS,
            severity=Severity.INFO,
            category=CheckCategory.TESTS,
            state=ResultState.VERIFIED_PASS,
            evidence=(evidence,),
        )

        value = finding.to_dict()

        self.assertEqual(value["assessment"], "pass")
        self.assertEqual(value["category"], "tests")
        self.assertEqual(value["state"], "verified_pass")
        self.assertEqual(value["evidence"][0]["command"]["command"]["args"], ["-m", "pytest"])
        self.assertTrue(execution.succeeded)
        self.assertEqual(json.loads(finding.to_json()), value)

    def test_json_output_is_stable_when_mapping_insertion_order_differs(self) -> None:
        first = CommandSpec(
            executable="tool",
            env={"Z_LAST": "2", "A_FIRST": "1"},
        )
        second = CommandSpec(
            executable="tool",
            env={"A_FIRST": "1", "Z_LAST": "2"},
        )

        self.assertEqual(dumps_json(first), dumps_json(second))


class FindingFingerprintTests(unittest.TestCase):
    def _finding(self, *, observed_at: str, message: str = "Lockfile is missing") -> Finding:
        evidence = Evidence(
            id="lockfile-observation",
            verification_class=VerificationClass.STATIC,
            summary="No lockfile was discovered",
            details={"observed_at": observed_at, "candidate_count": 0},
            observed_at=observed_at,
        )
        return Finding(
            check_id="dependencies.lockfile",
            title="Dependency lockfile missing",
            message=message,
            assessment=Assessment.FAIL,
            severity=Severity.HIGH,
            category=CheckCategory.DEPENDENCIES,
            state=ResultState.STATIC_FAIL,
            evidence=(evidence,),
        )

    def test_fingerprint_excludes_timestamps(self) -> None:
        earlier = self._finding(observed_at="2026-01-01T00:00:00Z")
        later = self._finding(observed_at="2026-01-02T00:00:00Z")

        self.assertEqual(finding_fingerprint(earlier), finding_fingerprint(later))
        self.assertEqual(earlier.fingerprint, later.fingerprint)
        self.assertEqual(len(earlier.fingerprint), 64)

    def test_fingerprint_changes_for_a_different_finding(self) -> None:
        original = self._finding(observed_at="2026-01-01T00:00:00Z")
        changed = self._finding(
            observed_at="2026-01-01T00:00:00Z",
            message="Dependency versions cannot be reproduced.",
        )

        self.assertNotEqual(original.fingerprint, changed.fingerprint)


class ContractShapeTests(unittest.TestCase):
    def test_report_and_receipt_have_versioned_shapes(self) -> None:
        repository = RepositoryIdentity(root="fixture-project", revision="abc123")
        content = ContentFingerprint(algorithm="sha256", digest="deadbeef", file_count=3)
        receipt = EvidenceReceipt(
            receipt_id="receipt-1",
            repository=repository,
            content=content,
            integrity=IntegrityMetadata(
                verified=True,
                content_fingerprint=content,
                method="manifest-sha256",
            ),
        )
        report = Report(repository=repository, evidence_receipt=receipt)

        report_value = report.to_dict()
        receipt_value = receipt.to_dict()

        self.assertEqual(report_value["schema_version"], "report-v1")
        self.assertEqual(receipt_value["schema_version"], "receipt-v1")
        self.assertEqual(report_value["evidence_receipt"]["receipt_id"], "receipt-1")

    def test_schema_documents_are_valid_json_with_expected_ids(self) -> None:
        for filename, expected_id in (
            ("report-v1.json", "https://reprocheck.dev/schemas/report-v1.json"),
            ("receipt-v1.json", "https://reprocheck.dev/schemas/receipt-v1.json"),
        ):
            with self.subTest(filename=filename):
                schema = json.loads((PROJECT_ROOT / "schemas" / filename).read_text())
                self.assertEqual(schema["$id"], expected_id)


if __name__ == "__main__":
    unittest.main()
