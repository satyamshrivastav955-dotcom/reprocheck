"""Meaningful, timestamp-insensitive comparison of evidence receipts."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import EvidenceReceipt, JSONValue, to_json_dict
from .canonical import _without_temporal_or_integrity_hash


def _normalized(value: object) -> JSONValue:
    converted = to_json_dict(value)
    return _without_temporal_or_integrity_hash(converted)


def _commands(receipt: EvidenceReceipt) -> JSONValue:
    return [
        _normalized(
            {
                "run_id": run.run_id,
                "commands": [item.command for item in run.commands],
            }
        )
        for run in receipt.verification_runs
        if run.commands
    ]


def _findings(receipt: EvidenceReceipt) -> JSONValue:
    findings: list[JSONValue] = []
    for item in receipt.evidence:
        finding = item.details.get("finding")
        if finding is not None:
            findings.append(_normalized(finding))
    return sorted(findings, key=repr)


def _outcomes(receipt: EvidenceReceipt) -> JSONValue:
    return _normalized(
        [
            {
                "run_id": run.run_id,
                "state": run.state,
                "error": run.error,
                "commands": [
                    {
                        "result_state": command.result_state,
                        "exit_code": command.exit_code,
                        "timed_out": command.timed_out,
                    }
                    for command in run.commands
                ],
            }
            for run in receipt.verification_runs
        ]
    )


def _environments(receipt: EvidenceReceipt) -> JSONValue:
    return _normalized(
        [
            {"run_id": run.run_id, "environment": run.environment}
            for run in receipt.verification_runs
            if run.environment is not None
        ]
    )


@dataclass(frozen=True)
class ReceiptComparison:
    """Category-level receipt differences, excluding timestamps."""

    changed: tuple[str, ...]
    content_changed: bool
    command_changed: bool
    finding_changed: bool
    outcome_changed: bool
    environment_changed: bool

    @property
    def equal(self) -> bool:
        return not self.changed

    @property
    def has_changes(self) -> bool:
        return bool(self.changed)


def compare_receipts(first: EvidenceReceipt, second: EvidenceReceipt) -> ReceiptComparison:
    """Compare content, commands, findings, outcomes, and environments."""

    content_changed = _normalized(first.content) != _normalized(second.content)
    command_changed = _commands(first) != _commands(second)
    finding_changed = _findings(first) != _findings(second)
    outcome_changed = _outcomes(first) != _outcomes(second)
    environment_changed = _environments(first) != _environments(second)
    changed = tuple(
        name
        for name, value in (
            ("content", content_changed),
            ("command", command_changed),
            ("finding", finding_changed),
            ("outcome", outcome_changed),
            ("environment", environment_changed),
        )
        if value
    )
    return ReceiptComparison(
        changed=changed,
        content_changed=content_changed,
        command_changed=command_changed,
        finding_changed=finding_changed,
        outcome_changed=outcome_changed,
        environment_changed=environment_changed,
    )


def compare(first: EvidenceReceipt, second: EvidenceReceipt) -> ReceiptComparison:
    """Compatibility alias for :func:`compare_receipts`."""

    return compare_receipts(first, second)


__all__ = ["ReceiptComparison", "compare", "compare_receipts"]
