import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from reprocheck.models import (
    Assessment,
    CheckCategory,
    CommandExecution,
    CommandSpec,
    Evidence,
    Finding,
    Report,
    RepositoryIdentity,
    ResultState,
    Severity,
    VerificationClass,
    VerificationRun,
)
from reprocheck.receipts import build_receipt, load_receipt, validate_receipt
from reprocheck.reporting import render_json, render_markdown, render_terminal
from reprocheck.scoring import score_report
from reprocheck.verification import execute_plan, plan_verification


def _finding(check_id, group, state=ResultState.LOOKS_CORRECT, assessment=Assessment.PASS):
    return Finding(
        check_id=check_id,
        title=check_id,
        message="static observation",
        category=CheckCategory.TESTS,
        severity=Severity.MEDIUM,
        scoring_group=group,
        state=state,
        assessment=assessment,
    )


def _report(root, *, failed=False, phase="verification", linked=True):
    command = CommandExecution(
        command=CommandSpec("uv", ("run", "python", "-m", "pytest")),
        result_state=ResultState.VERIFIED_FAIL if failed else ResultState.VERIFIED_PASS,
        exit_code=1 if failed else 0,
    )
    evidence = Evidence(
        id="run-test",
        check_id="RC-CMD-001",
        verification_class=VerificationClass.TEST,
        summary="Run test suite",
        details={"phase": phase},
        command=command,
    )
    run = VerificationRun(
        run_id="run",
        verification_class=VerificationClass.COMMAND,
        state=command.result_state,
        commands=(command,),
        evidence_ids=(evidence.id,) if linked else (),
    )
    findings = (
        _finding("RC-CMD-001", "commands"),
        _finding("RC-CMD-002", "commands", ResultState.STATIC_FAIL, Assessment.WARN),
        _finding("RC-DEP-002", "dependencies", ResultState.STATIC_FAIL, Assessment.FAIL),
        _finding("RC-RUN-001", "runtime"),
        _finding("RC-CON-001", "container", ResultState.NOT_VERIFIED, Assessment.UNSUPPORTED),
    )
    receipt = build_receipt(root, verification_runs=(run,), evidence=(evidence,), findings=findings)
    return Report(
        repository=RepositoryIdentity(root="."),
        findings=findings,
        verification_runs=(run,),
        evidence_receipt=receipt,
    )


class ExecutionScoreTests(unittest.TestCase):
    def test_linked_passing_test_has_only_command_verified_credit_and_static_score_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            report = _report(Path(directory))
            score = score_report(report)
            self.assertEqual(score.score, 15)
            self.assertEqual(score.group_scores["commands"], 0)
            self.assertEqual(score.verified_score, 15)
            self.assertEqual(score.verified_denominator, 55)
            self.assertEqual(
                score.verified_group_scores,
                {
                    "dependency_determinism": 0,
                    "runtime": 0,
                    "commands": 15,
                },
            )
            self.assertNotIn("container", score.applicable_groups)
            self.assertEqual(report.findings[1].state, ResultState.STATIC_FAIL)

    def test_failed_test_receives_zero_credit_but_does_not_lower_static_runtime_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            report = _report(Path(directory), failed=True)
            score = score_report(report)
            self.assertEqual(score.verified_score, 0)
            self.assertEqual(score.group_scores["runtime"], 15)
            self.assertEqual(score.verified_group_scores["runtime"], 0)

    def test_acquisition_unlinked_or_incoherent_execution_does_not_earn_credit(self):
        with tempfile.TemporaryDirectory() as directory:
            for options in ({"phase": "acquisition"}, {"linked": False}):
                with self.subTest(options=options):
                    self.assertEqual(
                        score_report(_report(Path(directory), **options)).verified_score, 0
                    )
            report = _report(Path(directory))
            receipt = report.evidence_receipt
            observation = next(item for item in receipt.evidence if item.command is not None)
            bad = replace(observation, command=replace(observation.command, exit_code=4))
            modified = replace(
                receipt,
                evidence=tuple(
                    bad if item.id == observation.id else item for item in receipt.evidence
                ),
            )
            self.assertEqual(
                score_report(replace(report, evidence_receipt=modified)).verified_score, 0
            )

    def test_missing_second_run_or_failed_second_test_cannot_earn_pair_credit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = _report(root)
            first = report.verification_runs[0]
            blocked = VerificationRun(run_id="run-2", verification_class=VerificationClass.COMMAND)
            score = score_report(replace(report, verification_runs=(first, blocked)))
            self.assertEqual(score.verified_score, 0)
            failed = _report(root, failed=True)
            second = replace(
                failed.verification_runs[0], run_id="run-2", evidence_ids=("run-2-test",)
            )
            observation = next(item for item in failed.evidence_receipt.evidence if item.command)
            evidence = (*report.evidence_receipt.evidence, replace(observation, id="run-2-test"))
            receipt = replace(report.evidence_receipt, evidence=evidence)
            self.assertEqual(
                score_report(
                    replace(
                        report,
                        verification_runs=(first, second),
                        evidence_receipt=receipt,
                    )
                ).verified_score,
                0,
            )

    def test_all_report_formats_show_identical_static_and_verified_scores(self):
        with tempfile.TemporaryDirectory() as directory:
            report = _report(Path(directory))
            payload = json.loads(render_json(report))
            self.assertEqual(payload["summary"]["score_summary"]["verified_score"], 15)
            self.assertIn("verified_score: 15/55", render_terminal(report))
            self.assertIn("**Verified score:** 15/55", render_markdown(report))
            self.assertIn("verified_group_scores:", render_terminal(report))
            self.assertIn("Verified score", render_markdown(report))
            self.assertEqual(payload["findings"][1]["state"], "static_fail")

    def test_receipt_round_trip_preserves_attribution_without_changing_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = _report(root)
            path = root / "receipt.json"
            path.write_text(report.evidence_receipt.to_json(), encoding="utf-8")
            receipt = load_receipt(path)
            self.assertTrue(validate_receipt(receipt).valid)
            self.assertEqual(receipt.schema_version, "receipt-v1")
            self.assertEqual(
                score_report(replace(report, evidence_receipt=receipt)).verified_score, 15
            )

    def test_executor_emits_explicit_check_id_only_for_supported_verification_steps(self):
        from unittest.mock import patch

        from reprocheck.verification.executor import _ProcessOutcome

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0.1'\n")
            (root / "uv.lock").write_text("version=1\n")
            (root / "tests").mkdir()
            (root / "tests/test_dummy.py").write_text("def test_dummy(): pass\n")
            plan = plan_verification(root, profile="python-uv")
            with patch(
                "reprocheck.verification.executor._run_argv",
                return_value=_ProcessOutcome(
                    exit_code=0,
                    stdout="",
                    stderr="",
                    timed_out=False,
                ),
            ):
                result = execute_plan(plan, approved=True, backend="host", allow_host_exec=True)
                unrelated = execute_plan(
                    replace(plan, profile="custom"),
                    approved=True,
                    backend="host",
                    allow_host_exec=True,
                )
            self.assertIsNone(result.evidence[0].check_id)
            self.assertEqual(result.evidence[1].check_id, "RC-CMD-001")
            self.assertTrue(all(item.check_id is None for item in unrelated.evidence))


if __name__ == "__main__":
    unittest.main()
