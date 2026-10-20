import json
import unittest
from dataclasses import replace

from reprocheck.models import (
    Assessment,
    CheckCategory,
    Evidence,
    Finding,
    Report,
    RepositoryIdentity,
    ResultState,
    Severity,
    VerificationClass,
)
from reprocheck.reporting import render_json, render_markdown, render_terminal


def _finding(
    check_id: str,
    path: str,
    *,
    state: ResultState,
    assessment: Assessment,
    severity: Severity,
    scoring_group: str,
    remediation: str,
) -> Finding:
    return Finding(
        check_id=check_id,
        title=f"{check_id} title",
        message=f"{check_id} message",
        assessment=assessment,
        severity=severity,
        category=CheckCategory.REPOSITORY,
        state=state,
        path=path,
        evidence=(
            Evidence(
                id=f"{check_id}-evidence",
                verification_class=VerificationClass.STATIC,
                summary="bounded observation",
                source=path,
            ),
        ),
        remediation=remediation,
        scoring_group=scoring_group,
    )


def _report() -> Report:
    first = _finding(
        "RC-ZZZ-001",
        "z.txt",
        state=ResultState.STATIC_FAIL,
        assessment=Assessment.FAIL,
        severity=Severity.HIGH,
        scoring_group="runtime",
        remediation="fix z",
    )
    second = _finding(
        "RC-AAA-001",
        "a.txt",
        state=ResultState.LOOKS_CORRECT,
        assessment=Assessment.PASS,
        severity=Severity.INFO,
        scoring_group="commands",
        remediation="none needed",
    )
    return Report(
        repository=RepositoryIdentity(root="fixture-project"),
        findings=(first, second),
        state=ResultState.NOT_VERIFIED,
        assessment=Assessment.UNKNOWN,
    )


class ReportingTests(unittest.TestCase):
    def test_renderers_are_deterministic_and_findings_are_sorted(self) -> None:
        report = _report()
        reversed_report = Report(
            repository=report.repository,
            findings=tuple(reversed(report.findings)),
            state=report.state,
            assessment=report.assessment,
        )

        self.assertEqual(render_terminal(report), render_terminal(reversed_report))
        self.assertEqual(render_json(report), render_json(reversed_report))
        self.assertEqual(render_markdown(report), render_markdown(reversed_report))

        terminal = render_terminal(report)
        self.assertLess(terminal.index("RC-AAA-001"), terminal.index("RC-ZZZ-001"))
        self.assertIn("state: static_fail", terminal)
        self.assertIn("assessment: fail", terminal)
        self.assertIn("severity: high", terminal)
        self.assertIn("z.txt: bounded observation", terminal)
        self.assertIn("remediation: fix z", terminal)
        self.assertIn("SCORE", terminal)
        self.assertIn("LIMITATIONS", terminal)

    def test_json_is_model_serialized_and_contains_machine_readable_score(self) -> None:
        output = render_json(_report())
        payload = json.loads(output)

        self.assertNotIn("REPROCHECK REPORT", output)
        self.assertEqual(payload["schema_version"], "report-v1")
        self.assertEqual(
            [finding["check_id"] for finding in payload["findings"]],
            ["RC-AAA-001", "RC-ZZZ-001"],
        )
        self.assertEqual(payload["findings"][1]["state"], "static_fail")
        self.assertEqual(payload["findings"][1]["evidence"][0]["source"], "z.txt")
        self.assertEqual(payload["summary"]["score_summary"]["score"], 15.0)
        self.assertIn("limitations", payload["summary"])

    def test_markdown_contains_finding_fields_and_limitations(self) -> None:
        output = render_markdown(_report())

        self.assertIn("# ReproCheck report", output)
        self.assertIn("## Score", output)
        self.assertIn("### RC-ZZZ-001", output)
        self.assertIn("**State:** `static_fail`", output)
        self.assertIn("**Assessment:** `fail`", output)
        self.assertIn("**Severity:** `high`", output)
        self.assertIn("**Evidence source:** z.txt", output)
        self.assertIn("**Remediation:** fix z", output)
        self.assertIn("## Limitations", output)

    def test_verification_metadata_is_visible_in_human_reports(self) -> None:
        report = replace(
            _report(),
            summary={
                "verification_status": "pass",
                "experiment_status": "reproducible",
                "experiment_comparison": {"artifact_equal": True},
                "experiment_run_outcomes": [
                    {
                        "run_id": "python-uv-run-1",
                        "status": "pass",
                        "state": "verified_pass",
                    },
                    {
                        "run_id": "python-uv-run-2",
                        "status": "pass",
                        "state": "verified_pass",
                    },
                ],
                "experiment_runs_passed": 2,
                "experiment_runs_failed": 0,
                "experiment_comparison_valid": True,
                "reproducibility_established": True,
                "experiment_exit_code": 0,
                "receipt_id": "receipt-demo",
            },
        )

        terminal = render_terminal(report)
        markdown = render_markdown(report)

        self.assertIn("verification: pass", terminal)
        self.assertIn("experiment: reproducible", terminal)
        self.assertIn("artifacts_match: true", terminal)
        self.assertIn("run 1 (python-uv-run-1): pass / verified_pass", terminal)
        self.assertIn("verification_runs: 2 (passed: 2, failed: 0)", terminal)
        self.assertIn("reproducibility: established", terminal)
        self.assertIn("receipt: receipt-demo", terminal)
        self.assertIn("**Experiment:** `reproducible`", markdown)
        self.assertIn("**Artifacts match:** `true`", markdown)
        self.assertIn("**Verification runs:** 2 (passed: 2, failed: 0)", markdown)
        self.assertIn("**Reproducibility:** `established`", markdown)
        self.assertIn("**Receipt:** `receipt-demo`", markdown)

    def test_drift_reports_passing_runs_and_reproducibility_not_established(self) -> None:
        report = replace(
            _report(),
            state=ResultState.VERIFIED_FAIL,
            assessment=Assessment.FAIL,
            summary={
                "verification_status": "pass",
                "experiment_status": "drift_detected",
                "experiment_comparison": {"artifact_equal": False},
                "experiment_run_outcomes": [
                    {"run_id": "python-uv-run-1", "status": "pass", "state": "verified_pass"},
                    {"run_id": "python-uv-run-2", "status": "pass", "state": "verified_pass"},
                ],
                "experiment_runs_passed": 2,
                "experiment_runs_failed": 0,
                "experiment_comparison_valid": True,
                "reproducibility_established": False,
                "experiment_exit_code": 1,
            },
        )

        terminal = render_terminal(report)
        markdown = render_markdown(report)
        payload = json.loads(render_json(report))

        self.assertIn("verification: pass", terminal)
        self.assertIn("experiment: drift_detected", terminal)
        self.assertIn("verification_runs: 2 (passed: 2, failed: 0)", terminal)
        self.assertIn("reproducibility: not established", terminal)
        self.assertIn("**Verification runs:** 2 (passed: 2, failed: 0)", markdown)
        self.assertIn("**Reproducibility:** `not established`", markdown)
        self.assertEqual(payload["summary"]["verification_status"], "pass")
        self.assertEqual(payload["summary"]["experiment_status"], "drift_detected")
        self.assertEqual(payload["summary"]["experiment_runs_passed"], 2)
        self.assertFalse(payload["summary"]["reproducibility_established"])
        self.assertEqual(payload["summary"]["experiment_exit_code"], 1)


if __name__ == "__main__":
    unittest.main()
