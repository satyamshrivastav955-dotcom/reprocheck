"""Deterministic Markdown rendering for ReproCheck reports."""

from __future__ import annotations

from ..models import Finding, Report
from ..scoring import SCORING_GROUP_WEIGHTS, ScoreSummary
from . import report_limitations, report_score, sorted_findings


def _md(value: object) -> str:
    if value is None:
        return "(none)"
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _sources(finding: Finding) -> str:
    values = sorted({evidence.source for evidence in finding.evidence if evidence.source})
    return ", ".join(values) if values else "(unspecified)"


def _score_value(value: float | None) -> str:
    if value is None:
        return "unavailable"
    return str(int(value)) if value.is_integer() else str(value)


def _render_score(score: ScoreSummary) -> list[str]:
    lines = [
        "## Score",
        "",
        f"- **Score:** {_score_value(score.score)}/{_score_value(score.denominator)} points",
        f"- **Coverage:** {score.coverage:.1%}",
        f"- **Verified score:** {_score_value(score.verified_score)}/"
        f"{_score_value(score.verified_denominator)}",
        f"- **Applicable groups:** {', '.join(score.applicable_groups) or '(none)'}",
        f"- **Excluded groups:** {', '.join(score.excluded_groups) or '(none)'}",
        "",
        "| Group | Score | Weight |",
        "| --- | ---: | ---: |",
    ]
    for group in score.applicable_groups:
        lines.append(
            f"| `{group}` | {_score_value(score.group_scores[group])} | "
            f"{SCORING_GROUP_WEIGHTS[group]} |"
        )
    if not score.applicable_groups:
        lines.append("| (none) | 0 | 0 |")
    return lines


def _render_finding(finding: Finding) -> list[str]:
    lines = [
        f"### {_md(finding.check_id)} — {_md(finding.title)}",
        "",
        f"- **Path:** `{_md(finding.path or '(no path)')}`",
        f"- **State:** `{_md(finding.state.value)}`",
        f"- **Assessment:** `{_md(finding.assessment.value)}`",
        f"- **Severity:** `{_md(finding.severity.value)}`",
        f"- **Message:** {_md(finding.message)}",
        f"- **Evidence source:** {_md(_sources(finding))}",
        f"- **Remediation:** {_md(finding.remediation)}",
        "",
    ]
    return lines


def render_markdown(
    report: Report,
    score_summary: ScoreSummary | None = None,
) -> str:
    """Render a report as deterministic Markdown."""

    score = report_score(report, score_summary)
    lines = [
        "# ReproCheck report",
        "",
        f"- **Repository:** `{_md(report.repository.root)}`",
        f"- **State:** `{_md(report.state.value)}`",
        f"- **Assessment:** `{_md(report.assessment.value)}`",
    ]
    verification_status = report.summary.get("verification_status")
    receipt_id = report.summary.get("receipt_id")
    experiment_status = report.summary.get("experiment_status")
    experiment_comparison = report.summary.get("experiment_comparison")
    if isinstance(verification_status, str):
        lines.append(f"- **Verification:** `{_md(verification_status)}`")
    if isinstance(experiment_status, str):
        lines.append(f"- **Experiment:** `{_md(experiment_status)}`")
    if isinstance(experiment_comparison, dict):
        artifact_equal = experiment_comparison.get("artifact_equal")
        if isinstance(artifact_equal, bool):
            lines.append(f"- **Artifacts match:** `{str(artifact_equal).lower()}`")
    if isinstance(receipt_id, str):
        lines.append(f"- **Receipt:** `{_md(receipt_id)}`")
    lines.extend(["", *_render_score(score), "## Findings", ""])
    findings = sorted_findings(report.findings)
    if findings:
        for finding in findings:
            lines.extend(_render_finding(finding))
    else:
        lines.extend(["No findings.", ""])
    lines.extend(["## Limitations", ""])
    limitations = report_limitations(report, score)
    if limitations:
        lines.extend(f"- {_md(limitation)}" for limitation in limitations)
    else:
        lines.append("- None")
    return "\n".join(lines).rstrip() + "\n"


def render_markdown_report(
    report: Report,
    score_summary: ScoreSummary | None = None,
) -> str:
    """Named alias for :func:`render_markdown`."""

    return render_markdown(report, score_summary)


def to_markdown(report: Report, score_summary: ScoreSummary | None = None) -> str:
    """Short alias for :func:`render_markdown`."""

    return render_markdown(report, score_summary)


__all__ = ["render_markdown", "render_markdown_report", "to_markdown"]
