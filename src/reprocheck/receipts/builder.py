"""Build model-compatible evidence receipts from verification observations."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from ..discovery import build_repository_identity
from ..fingerprint import compute_content_fingerprint
from ..models import (
    CommandExecution,
    CommandSpec,
    EnvironmentFingerprint,
    Evidence,
    EvidenceReceipt,
    Finding,
    IntegrityMetadata,
    JSONValue,
    ResultState,
    VerificationClass,
    VerificationRun,
    to_json_dict,
)
from ..verification.executor import ExecutionResult, sanitize_environment
from ..verification.planner import VerificationPlan
from ..verification.redaction import bounded_redact
from .canonical import CANONICAL_HASH_KEY, canonical_hash


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _deduplicate_limitations(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({bounded_redact(value, max_chars=1000) for value in values if value}))


def _finding_evidence(finding: Finding) -> Evidence:
    return Evidence(
        id=f"finding-{finding.fingerprint}",
        verification_class=VerificationClass.STATIC,
        summary=finding.title,
        check_id=finding.check_id,
        details={"finding": to_json_dict(finding)},
        source=finding.path,
    )


def _redacted_command(command: CommandSpec) -> CommandSpec:
    return CommandSpec(
        executable=command.executable,
        args=command.args,
        cwd=command.cwd,
        env=sanitize_environment(source={}, overrides=command.env),
        timeout_seconds=command.timeout_seconds,
        description=command.description,
    )


def _redacted_execution(execution: CommandExecution) -> CommandExecution:
    return CommandExecution(
        command=_redacted_command(execution.command),
        result_state=execution.result_state,
        exit_code=execution.exit_code,
        stdout=bounded_redact(execution.stdout),
        stderr=bounded_redact(execution.stderr),
        started_at=execution.started_at,
        finished_at=execution.finished_at,
        duration_ms=execution.duration_ms,
        timed_out=execution.timed_out,
    )


def _redacted_run(run: VerificationRun) -> VerificationRun:
    return VerificationRun(
        run_id=run.run_id,
        verification_class=run.verification_class,
        state=run.state,
        commands=tuple(_redacted_execution(command) for command in run.commands),
        environment=run.environment,
        evidence_ids=run.evidence_ids,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error=bounded_redact(run.error) if run.error else None,
    )


def _redacted_evidence(item: Evidence) -> Evidence:
    return Evidence(
        id=item.id,
        verification_class=item.verification_class,
        summary=bounded_redact(item.summary, max_chars=1000),
        check_id=item.check_id,
        details=item.details,
        source=item.source,
        command=_redacted_execution(item.command) if item.command is not None else None,
        observed_at=item.observed_at,
    )


def build_receipt(
    repository: Path,
    *,
    plan: VerificationPlan | None = None,
    result: ExecutionResult | None = None,
    profile: str | None = None,
    environment: EnvironmentFingerprint | None = None,
    commands: Iterable[CommandExecution] = (),
    verification_runs: Iterable[VerificationRun] = (),
    evidence: Iterable[Evidence] = (),
    findings: Iterable[Finding] = (),
    limitations: Iterable[str] = (),
    receipt_id: str | None = None,
    generated_at: str | None = None,
) -> EvidenceReceipt:
    """Generate a receipt containing fingerprinted, bounded observations.

    Execution output is already bounded by the executor; this function applies
    redaction again at the model boundary and records profile/network metadata
    in the existing integrity-details object without changing the receipt
    schema.
    """

    content = compute_content_fingerprint(repository)
    identity = build_repository_identity(repository)
    selected_profile = (
        profile
        or (result.profile if result is not None else None)
        or (plan.profile if plan is not None else "unspecified")
    )

    run_items = [_redacted_run(item) for item in verification_runs]
    evidence_items = [_redacted_evidence(item) for item in evidence]
    limitation_values = list(limitations)
    if plan is not None:
        limitation_values.extend(plan.limitations)
    if result is not None:
        run_items.append(_redacted_run(result.run))
        evidence_items.extend(_redacted_evidence(item) for item in result.evidence)
        limitation_values.extend(result.limitations)
        environment = environment or result.environment

    command_items = tuple(_redacted_execution(item) for item in commands)
    if command_items and not run_items:
        run_items.append(
            VerificationRun(
                run_id=f"{selected_profile}-run",
                verification_class=VerificationClass.COMMAND,
                state=(
                    ResultState.VERIFIED_PASS
                    if all(command.succeeded for command in command_items)
                    else ResultState.VERIFIED_FAIL
                ),
                commands=command_items,
                environment=environment,
            )
        )
    if environment is not None and not run_items:
        run_items.append(
            VerificationRun(
                run_id=f"{selected_profile}-environment",
                verification_class=VerificationClass.ENVIRONMENT,
                environment=environment,
            )
        )

    evidence_items.extend(_finding_evidence(finding) for finding in findings)
    deduplicated_evidence = {item.id: item for item in evidence_items}
    receipt_evidence = tuple(deduplicated_evidence[key] for key in sorted(deduplicated_evidence))
    receipt_runs = tuple(sorted(run_items, key=lambda item: item.run_id))
    normalized_limitations = _deduplicate_limitations(limitation_values)

    details: dict[str, JSONValue] = {
        "profile": selected_profile,
        "limitations": list(normalized_limitations),
    }
    if plan is not None:
        details["acquisition"] = {
            "phase": plan.acquisition.phase.value,
            "network_required": plan.acquisition_network_required,
            "reason": plan.acquisition.reason,
        }
        details["verification_network_required"] = plan.verification_network_required
    if result is not None:
        details["outcome"] = result.status.value
    if environment is not None:
        details["environment"] = to_json_dict(environment)

    integrity = IntegrityMetadata(
        verified=True,
        content_fingerprint=content,
        method="canonical-json-sha256",
        details=details,
    )
    selected_id = receipt_id or f"receipt-{content.digest[:16]}-{selected_profile}"
    unsigned = EvidenceReceipt(
        receipt_id=selected_id,
        repository=identity,
        content=content,
        integrity=integrity,
        evidence=receipt_evidence,
        verification_runs=receipt_runs,
        generated_at=generated_at or _now(),
    )
    final_details = dict(details)
    final_details[CANONICAL_HASH_KEY] = canonical_hash(unsigned)
    final_integrity = IntegrityMetadata(
        verified=True,
        content_fingerprint=content,
        method="canonical-json-sha256",
        details=final_details,
    )
    final_receipt = EvidenceReceipt(
        receipt_id=unsigned.receipt_id,
        repository=unsigned.repository,
        content=unsigned.content,
        integrity=final_integrity,
        evidence=unsigned.evidence,
        verification_runs=unsigned.verification_runs,
        schema_version=unsigned.schema_version,
        generated_at=unsigned.generated_at,
    )
    # The hash key is excluded by canonicalization, so this is a useful guard
    # against an accidental change to the canonical hash policy.
    if canonical_hash(final_receipt) != final_details[CANONICAL_HASH_KEY]:
        raise RuntimeError("receipt canonical hash changed while attaching integrity metadata")
    return final_receipt


generate_receipt = build_receipt


__all__ = ["build_receipt", "generate_receipt"]
