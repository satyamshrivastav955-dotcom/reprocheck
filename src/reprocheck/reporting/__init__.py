"""Deterministic renderers for :class:`reprocheck.models.Report`."""

from __future__ import annotations

from collections.abc import Iterable

from ..models import Finding, Report
from ..scoring import ScoreSummary, score_report


def sorted_findings(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    """Return findings in the stable presentation order required by reports."""

    return tuple(
        sorted(
            findings,
            key=lambda finding: (
                finding.check_id,
                finding.path or "",
                finding.fingerprint,
            ),
        )
    )


def report_score(report: Report, score_summary: ScoreSummary | None = None) -> ScoreSummary:
    """Use an explicitly supplied score or calculate the report's score."""

    return score_summary if score_summary is not None else score_report(report)


def report_limitations(
    report: Report, score_summary: ScoreSummary | None = None
) -> tuple[str, ...]:
    """Collect deterministic report and scoring limitations without duplicates."""

    limitations: set[str] = set()
    for check in report.checks:
        limitations.update(check.limitations)
    score = report_score(report, score_summary)
    limitations.update(score.limitations)

    # Preserve limitations supplied by an upstream report producer when they
    # are represented in the existing summary mapping.
    supplied = report.summary.get("limitations")
    if isinstance(supplied, str):
        limitations.add(supplied)
    elif isinstance(supplied, list):
        limitations.update(item for item in supplied if isinstance(item, str))
    return tuple(sorted(limitations))


from .json_report import render_json, render_json_report, report_to_dict  # noqa: E402
from .markdown import render_markdown, render_markdown_report, to_markdown  # noqa: E402
from .terminal import render_terminal, render_terminal_report, to_terminal  # noqa: E402

__all__ = [
    "render_json",
    "render_json_report",
    "render_markdown",
    "render_markdown_report",
    "render_terminal",
    "render_terminal_report",
    "report_limitations",
    "report_score",
    "report_to_dict",
    "sorted_findings",
    "to_markdown",
    "to_terminal",
]
