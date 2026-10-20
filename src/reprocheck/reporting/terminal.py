"""Plain terminal rendering for ReproCheck reports."""

from __future__ import annotations

from ..models import Finding, Report
from ..scoring import SCORING_GROUP_WEIGHTS, ScoreSummary
from . import report_limitations, report_score, sorted_findings


def _value(value: object) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _sources(finding: Finding) -> tuple[str, ...]:
    sources = sorted({evidence.source for evidence in finding.evidence if evidence.source})
    return tuple(sources) or ("(unspecified)",)


def _render_score(score: ScoreSummary) -> list[str]:
    applicable = ", ".join(score.applicable_groups) or "none"
    excluded = ", ".join(score.excluded_groups) or "none"
    lines = [
        "SCORE",
        f"  score: {_value(score.score)}/{_value(score.denominator)} points",
        f"  coverage: {score.coverage:.1%}",
        f"  verified_score: {_value(score.verified_score)}/{_value(score.verified_denominator)}",
        f"  applicable_groups: {applicable}",
        f"  excluded_groups: {excluded}",
        "  group_scores:",
    ]
    for group in score.applicable_groups:
        lines.append(
            f"    {group}: {_value(score.group_scores[group])}/{SCORING_GROUP_WEIGHTS[group]}"
        )
    return lines


def _render_finding(finding: Finding) -> list[str]:
    lines = [
        f"- {finding.check_id} [{finding.path or '(no path)'}]",
        f"  title: {finding.title}",
        f"  state: {finding.state.value}",
        f"  assessment: {finding.assessment.value}",
        f"  severity: {finding.severity.value}",
        f"  message: {finding.message}",
        "  evidence:",
    ]
    for evidence in finding.evidence:
        source = evidence.source or "(unspecified)"
        lines.append(f"    - {source}: {evidence.summary}")
    if not finding.evidence:
        lines.append("    - (none)")
    lines.append(f"  evidence_sources: {', '.join(_sources(finding))}")
    lines.append(f"  remediation: {finding.remediation or '(none)'}")
    return lines


def render_terminal(
    report: Report,
    score_summary: ScoreSummary | None = None,
) -> str:
    """Render a report as deterministic plain text suitable for a terminal."""

    score = report_score(report, score_summary)
    lines = [
        "REPROCHECK REPORT",
        f"repository: {report.repository.root}",
        f"state: {report.state.value}",
        f"assessment: {report.assessment.value}",
        f"findings: {len(report.findings)}",
    ]
    verification_status = report.summary.get("verification_status")
    receipt_id = report.summary.get("receipt_id")
    experiment_status = report.summary.get("experiment_status")
    experiment_comparison = report.summary.get("experiment_comparison")
    if isinstance(verification_status, str):
        lines.append(f"verification: {verification_status}")
    if isinstance(experiment_status, str):
        lines.append(f"experiment: {experiment_status}")
    if isinstance(experiment_comparison, dict):
        artifact_equal = experiment_comparison.get("artifact_equal")
        if isinstance(artifact_equal, bool):
            lines.append(f"artifacts_match: {str(artifact_equal).lower()}")
    if isinstance(receipt_id, str):
        lines.append(f"receipt: {receipt_id}")
    lines.extend(["", *_render_score(score), "", "FINDINGS"])
    findings = sorted_findings(report.findings)
    if findings:
        for finding in findings:
            lines.extend(_render_finding(finding))
    else:
        lines.append("- none")
    lines.extend(["", "LIMITATIONS"])
    limitations = report_limitations(report, score)
    if limitations:
        lines.extend(f"- {limitation}" for limitation in limitations)
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def render_terminal_report(
    report: Report,
    score_summary: ScoreSummary | None = None,
) -> str:
    """Named alias for :func:`render_terminal`."""

    return render_terminal(report, score_summary)


def to_terminal(report: Report, score_summary: ScoreSummary | None = None) -> str:
    """Short alias for :func:`render_terminal`."""

    return render_terminal(report, score_summary)


__all__ = ["render_terminal", "render_terminal_report", "to_terminal"]
