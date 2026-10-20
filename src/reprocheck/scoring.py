"""Deterministic, evidence-aware scoring for static ReproCheck findings.

Scoring is deliberately kept separate from check execution.  Checks declare a
``scoring_group`` on their existing :class:`~reprocheck.models.CheckSpec` and
findings carry the same value.  This module maps the existing check group names
to the documented, non-overlapping score groups and scores each group once.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .models import (
    Assessment,
    CheckSpec,
    CommandExecution,
    Evidence,
    Finding,
    JsonModel,
    Report,
    ResultState,
    VerificationClass,
    VerificationRun,
)

# These are the only score groups.  Their weights intentionally sum to 100,
# which makes ``score`` a points value while ``denominator`` records how many
# points were actually applicable to the repository.
SCORING_GROUP_WEIGHTS: Mapping[str, int] = MappingProxyType(
    {
        "dependency_determinism": 25,
        "runtime": 15,
        "commands": 15,
        "environment": 10,
        "ci": 10,
        "repository": 10,
        "container": 10,
        "documentation": 5,
    }
)
TOTAL_WEIGHT = sum(SCORING_GROUP_WEIGHTS.values())


# Existing checks use descriptive group names.  The aliases below are the
# compatibility boundary: changing this table does not change any check or
# alter the meaning of a finding.  Each existing group maps to exactly one
# weighted group, so findings cannot be counted in overlapping buckets.
SCORING_GROUP_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "dependency_determinism": "dependency_determinism",
        "dependencies": "dependency_determinism",
        "runtime": "runtime",
        "commands": "commands",
        "environment": "environment",
        "ci": "ci",
        "repository": "repository",
        "repository-hygiene": "repository",
        "discovery": "repository",
        "portability": "repository",
        "security": "repository",
        "container": "container",
        "documentation": "documentation",
        "package-metadata": "documentation",
    }
)

_EXCLUDED_ASSESSMENTS = frozenset({Assessment.UNSUPPORTED, Assessment.NOT_APPLICABLE})
_VERIFIED_STATES = frozenset({ResultState.VERIFIED_PASS, ResultState.VERIFIED_FAIL})
_GROUP_ORDER = tuple(SCORING_GROUP_WEIGHTS)


@dataclass(frozen=True)
class ScoreSummary(JsonModel):
    """The static and, when available, executed-phase score summaries.

    ``score`` and ``denominator`` are weighted points, not percentages.  The
    denominator only includes applicable groups.  ``coverage`` is the portion
    of the documented 100-point score that was applicable, represented as a
    value between 0 and 1.

    ``verified_score`` remains ``None`` until at least one non-static
    verification phase is represented by a :class:`VerificationRun`.  Once a
    phase exists it uses the same applicable-group denominator, with groups
    lacking a verified outcome scoring zero and being called out in
    ``limitations``. Linked, recognized test/build command evidence attributes
    only RC-CMD-001 execution to the commands group; it never changes the
    original static findings or verifies dependency closure or artifact equality.
    """

    score: float
    denominator: float
    verified_score: float | None
    verified_denominator: float | None
    applicable_groups: tuple[str, ...]
    excluded_groups: tuple[str, ...]
    coverage: float
    verified_coverage: float | None
    group_scores: Mapping[str, float]
    verified_group_scores: Mapping[str, float]
    limitations: tuple[str, ...]


def canonical_scoring_group(scoring_group: str | None) -> str | None:
    """Return the documented weighted group for an existing check group."""

    if scoring_group is None:
        return None
    return SCORING_GROUP_ALIASES.get(scoring_group)


def _state_factor(state: ResultState) -> float:
    if state in {ResultState.LOOKS_CORRECT, ResultState.VERIFIED_PASS}:
        return 1.0
    if state == ResultState.PRESENT:
        return 0.5
    # STATIC_FAIL and NOT_VERIFIED deliberately have no positive contribution.
    return 0.0


def _best_group_factor(findings: Iterable[Finding]) -> float:
    """Return the least optimistic factor for one non-overlapping group."""

    return min(_state_factor(finding.state) for finding in findings)


def _has_executed_phase(verification_runs: Iterable[VerificationRun]) -> bool:
    """Whether a report contains an executed, non-static verification phase."""

    return any(run.verification_class != VerificationClass.STATIC for run in verification_runs)


def _append_unique(target: set[str], values: Iterable[str]) -> None:
    target.update(value for value in values if value)


def _execution_identity(command: CommandExecution) -> tuple[object, ...]:
    # Receipt redaction may normalize logs and environments; neither changes
    # which argv ran or its operational result.
    return (
        command.command.executable,
        command.command.args,
        command.command.cwd,
        command.result_state,
        command.exit_code,
        command.timed_out,
    )


def _command_execution_factor(
    runs: tuple[VerificationRun, ...], evidence: Iterable[Evidence]
) -> float | None:
    """Score linked RC-CMD-001 test/build evidence across every command run.

    This is execution coverage, not promotion of the original static findings.
    Legacy receipts without explicit attribution remain valid but earn no new
    inferred credit. Run membership and the actual recorded command must agree.
    """

    observations = tuple(
        item
        for item in evidence
        if item.check_id == "RC-CMD-001"
        and item.verification_class in {VerificationClass.TEST, VerificationClass.BUILD}
        and item.details.get("phase") == "verification"
        and item.command is not None
    )
    if not observations:
        return None
    factors: list[float] = []
    for run in runs:
        if run.verification_class not in {
            VerificationClass.COMMAND,
            VerificationClass.TEST,
            VerificationClass.BUILD,
        }:
            continue
        linked = [
            item.command
            for item in observations
            if item.id in run.evidence_ids
            and item.command is not None
            and any(
                _execution_identity(item.command) == _execution_identity(command)
                for command in run.commands
            )
        ]
        if not linked:
            factors.append(0.0)
            continue
        factors.append(
            float(
                all(
                    command is not None
                    and command.result_state is ResultState.VERIFIED_PASS
                    and command.exit_code == 0
                    and not command.timed_out
                    for command in linked
                )
            )
        )
    return min(factors) if factors else None


def score_findings(
    findings: Iterable[Finding],
    *,
    checks: Iterable[CheckSpec] = (),
    verification_runs: Iterable[VerificationRun] = (),
    evidence: Iterable[Evidence] = (),
) -> ScoreSummary:
    """Score findings once per documented group.

    Unsupported and not-applicable findings are omitted before grouping.  If
    several applicable findings map to one group, the group's worst state is
    used; this prevents a duplicated check result from subtracting the group's
    weight more than once.  An unknown or missing ``scoring_group`` is not
    silently assigned a weight.
    """

    finding_items = tuple(findings)
    check_items = tuple(checks)
    runs = tuple(verification_runs)
    groups: dict[str, list[Finding]] = {group: [] for group in _GROUP_ORDER}
    verified_groups: dict[str, list[Finding]] = {group: [] for group in _GROUP_ORDER}
    excluded_findings: dict[str, list[Finding]] = {group: [] for group in _GROUP_ORDER}
    limitations: set[str] = set()

    check_by_id = {check.check_id: check for check in check_items}
    for check in check_items:
        if check.limitations:
            _append_unique(limitations, check.limitations)

    for finding in finding_items:
        source_group = finding.scoring_group
        group = canonical_scoring_group(source_group)
        if group is None:
            if source_group is None:
                limitations.add(
                    f"Finding {finding.check_id} has no scoring_group and was excluded "
                    "from scoring."
                )
            else:
                limitations.add(
                    f"Finding {finding.check_id} declares unknown scoring group "
                    f"{source_group!r} and was excluded from scoring."
                )
            continue

        if finding.assessment in _EXCLUDED_ASSESSMENTS:
            excluded_findings[group].append(finding)
            continue

        groups[group].append(finding)
        if finding.state in _VERIFIED_STATES:
            verified_groups[group].append(finding)

    applicable = tuple(group for group in _GROUP_ORDER if groups[group])
    excluded = tuple(group for group in _GROUP_ORDER if not groups[group])

    for group in excluded:
        if excluded_findings[group]:
            limitations.add(
                f"Scoring group '{group}' is excluded because its findings are "
                "unsupported or not applicable."
            )
        else:
            limitations.add(f"Scoring group '{group}' has no applicable finding and is excluded.")

    group_scores = {
        group: SCORING_GROUP_WEIGHTS[group] * _best_group_factor(groups[group])
        for group in applicable
    }
    denominator = float(sum(SCORING_GROUP_WEIGHTS[group] for group in applicable))
    score = float(sum(group_scores.values()))
    coverage = denominator / TOTAL_WEIGHT if TOTAL_WEIGHT else 0.0

    for group in applicable:
        states = {finding.state for finding in groups[group]}
        if ResultState.PRESENT in states:
            limitations.add(
                f"Scoring group '{group}' has presence-only evidence capped at 50% of its weight."
            )
        if ResultState.NOT_VERIFIED in states:
            limitations.add(
                f"Scoring group '{group}' contains NOT_VERIFIED evidence; its contribution "
                "is zero and remains uncertain."
            )

    if _has_executed_phase(runs):
        verified_group_scores: dict[str, float] = {}
        command_factor = _command_execution_factor(runs, evidence)
        for group in applicable:
            factors = [_state_factor(item.state) for item in verified_groups[group]]
            if (
                group == "commands"
                and command_factor is not None
                and any(item.check_id == "RC-CMD-001" for item in groups[group])
            ):
                factors.append(command_factor)
            if factors:
                verified_group_scores[group] = SCORING_GROUP_WEIGHTS[group] * min(factors)
            else:
                verified_group_scores[group] = 0.0
                limitations.add(
                    f"Scoring group '{group}' has no verified outcome; its verified "
                    "contribution is zero and uncertain."
                )
        verified_denominator: float | None = denominator
        verified_score: float | None = float(sum(verified_group_scores.values()))
        verified_coverage: float | None = coverage
    else:
        verified_group_scores = {}
        verified_denominator = None
        verified_score = None
        verified_coverage = None
        limitations.add(
            "No executed verification phase is available; the verified score is unavailable."
        )

    # A check's declared limitations are useful context, while sorting makes
    # the summary stable regardless of registry or finding input order.
    for finding in finding_items:
        declared_check = check_by_id.get(finding.check_id)
        if declared_check is not None:
            _append_unique(limitations, declared_check.limitations)

    return ScoreSummary(
        score=score,
        denominator=denominator,
        verified_score=verified_score,
        verified_denominator=verified_denominator,
        applicable_groups=applicable,
        excluded_groups=excluded,
        coverage=coverage,
        verified_coverage=verified_coverage,
        group_scores=group_scores,
        verified_group_scores=verified_group_scores,
        limitations=tuple(sorted(limitations)),
    )


def score_report(report: Report) -> ScoreSummary:
    """Return the score summary for a modelled report."""

    return score_findings(
        report.findings,
        checks=report.checks,
        verification_runs=report.verification_runs,
        evidence=report.evidence_receipt.evidence if report.evidence_receipt is not None else (),
    )


def calculate_score(
    findings: Iterable[Finding],
    *,
    checks: Iterable[CheckSpec] = (),
    verification_runs: Iterable[VerificationRun] = (),
) -> ScoreSummary:
    """Compatibility alias for :func:`score_findings`."""

    return score_findings(
        findings,
        checks=checks,
        verification_runs=verification_runs,
    )


__all__ = [
    "SCORING_GROUP_ALIASES",
    "SCORING_GROUP_WEIGHTS",
    "ScoreSummary",
    "TOTAL_WEIGHT",
    "calculate_score",
    "canonical_scoring_group",
    "score_findings",
    "score_report",
]
