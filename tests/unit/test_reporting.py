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
            summary={"verification_status": "pass", "receipt_id": "receipt-demo"},
        )

        terminal = render_terminal(report)
        markdown = render_markdown(report)

        self.assertIn("verification: pass", terminal)
        self.assertIn("receipt: receipt-demo", terminal)
        self.assertIn("**Receipt:** `receipt-demo`", markdown)


if __name__ == "__main__":
    unittest.main()
