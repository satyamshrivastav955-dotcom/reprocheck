"""Machine-readable JSON rendering for ReproCheck reports."""

from __future__ import annotations

from ..models import JSONValue, Report, dumps_json
from ..scoring import ScoreSummary
from . import report_limitations, report_score, sorted_findings


def report_to_dict(
    report: Report,
    score_summary: ScoreSummary | None = None,
) -> dict[str, JSONValue]:
    """Return a serialized report with sorted findings and score metadata.

    The report itself is serialized by the existing model serializer.  The
    score and rendered limitations live in the existing ``summary`` object so
    the result remains a ``report-v1`` shaped object rather than adding an
    unrelated top-level protocol field.
    """

    score = report_score(report, score_summary)
    payload = report.to_dict()
    payload["findings"] = [finding.to_dict() for finding in sorted_findings(report.findings)]
    summary_value = payload.get("summary", {})
    summary = dict(summary_value) if isinstance(summary_value, dict) else {}
    summary["score_summary"] = score.to_dict()
    summary["limitations"] = list(report_limitations(report, score))
    payload["summary"] = summary
    return payload


def render_json(
    report: Report,
    score_summary: ScoreSummary | None = None,
    *,
    indent: int | None = 2,
) -> str:
    """Render one report as deterministic machine-readable JSON."""

    return dumps_json(report_to_dict(report, score_summary), indent=indent)


def render_json_report(
    report: Report,
    score_summary: ScoreSummary | None = None,
    *,
    indent: int | None = 2,
) -> str:
    """Named alias for :func:`render_json`."""

    return render_json(report, score_summary, indent=indent)


__all__ = ["render_json", "render_json_report", "report_to_dict"]
