import unittest

from reprocheck.models import (
    Assessment,
    CheckCategory,
    CheckSpec,
    Evidence,
    Finding,
    ResultState,
    Severity,
    VerificationClass,
    VerificationRun,
)
from reprocheck.scoring import (
    SCORING_GROUP_WEIGHTS,
    ScoreSummary,
    score_findings,
)


def _finding(
    check_id: str,
    scoring_group: str,
    state: ResultState,
    *,
    assessment: Assessment = Assessment.PASS,
    path: str | None = None,
) -> Finding:
    return Finding(
        check_id=check_id,
        title=check_id,
        message="fixture finding",
        assessment=assessment,
        severity=Severity.MEDIUM,
        category=CheckCategory.REPOSITORY,
        state=state,
        path=path,
        evidence=(
            Evidence(
                id=f"{check_id}-evidence",
                verification_class=VerificationClass.STATIC,
                summary="fixture evidence",
                source=path,
            ),
        ),
        scoring_group=scoring_group,
    )


class ScoringTests(unittest.TestCase):
    def test_weights_are_documented_and_sum_to_one_hundred(self) -> None:
        self.assertEqual(
            SCORING_GROUP_WEIGHTS,
            {
                "dependency_determinism": 25,
                "runtime": 15,
                "commands": 15,
                "environment": 10,
                "ci": 10,
                "repository": 10,
                "container": 10,
                "documentation": 5,
            },
        )
        self.assertEqual(sum(SCORING_GROUP_WEIGHTS.values()), 100)

    def test_duplicate_findings_share_one_group_and_presence_is_capped(self) -> None:
        summary = score_findings(
            (
                _finding("RC-DEP-001", "dependencies", ResultState.STATIC_FAIL),
                _finding("RC-DEP-002", "dependencies", ResultState.STATIC_FAIL),
                _finding("RC-RUN-001", "runtime", ResultState.LOOKS_CORRECT),
                _finding("RC-ENV-001", "environment", ResultState.PRESENT),
                _finding(
                    "RC-CI-001",
                    "ci",
                    ResultState.NOT_VERIFIED,
                    assessment=Assessment.UNSUPPORTED,
                ),
            )
        )

        self.assertIsInstance(summary, ScoreSummary)
        self.assertEqual(
            summary.applicable_groups, ("dependency_determinism", "runtime", "environment")
        )
        self.assertEqual(
            summary.excluded_groups,
            ("commands", "ci", "repository", "container", "documentation"),
        )
        self.assertEqual(summary.denominator, 50.0)
        self.assertEqual(summary.score, 20.0)
        self.assertEqual(summary.group_scores["dependency_determinism"], 0.0)
        self.assertEqual(summary.group_scores["environment"], 5.0)
        self.assertEqual(summary.coverage, 0.5)
        self.assertIsNone(summary.verified_score)
        self.assertTrue(any("presence-only" in item for item in summary.limitations))

    def test_not_verified_is_zero_with_uncertainty_and_verified_score_is_separate(self) -> None:
        checks = (
            CheckSpec(
                check_id="RC-RUN-001",
                name="runtime",
                category=CheckCategory.RUNTIME,
                verification_class=VerificationClass.STATIC,
                limitations=("Static runtime evidence does not execute the runtime.",),
                scoring_group="runtime",
            ),
        )
        static_summary = score_findings(
            (_finding("RC-RUN-001", "runtime", ResultState.NOT_VERIFIED),),
            checks=checks,
        )
        self.assertEqual(static_summary.score, 0.0)
        self.assertTrue(any("remains uncertain" in item for item in static_summary.limitations))
        self.assertIsNone(static_summary.verified_score)

        executed_summary = score_findings(
            (_finding("RC-RUN-001", "runtime", ResultState.VERIFIED_PASS),),
            checks=checks,
            verification_runs=(
                VerificationRun(run_id="runtime-1", verification_class=VerificationClass.COMMAND),
            ),
        )
        self.assertEqual(executed_summary.score, 15.0)
        self.assertEqual(executed_summary.verified_score, 15.0)
        self.assertEqual(executed_summary.verified_denominator, 15.0)
        self.assertEqual(executed_summary.verified_coverage, executed_summary.coverage)

    def test_unsupported_and_unknown_groups_do_not_receive_weight(self) -> None:
        summary = score_findings(
            (
                _finding(
                    "RC-ENV-001",
                    "environment",
                    ResultState.STATIC_FAIL,
                    assessment=Assessment.NOT_APPLICABLE,
                ),
                _finding("RC-UNKNOWN-001", "future-group", ResultState.STATIC_FAIL),
            )
        )

        self.assertEqual(summary.score, 0.0)
        self.assertEqual(summary.denominator, 0.0)
        self.assertNotIn("environment", summary.applicable_groups)
        self.assertIn("environment", summary.excluded_groups)
        self.assertTrue(any("unknown scoring group" in item for item in summary.limitations))


if __name__ == "__main__":
    unittest.main()
