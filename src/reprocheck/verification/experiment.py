"""Two-run reproducibility experiments built on the existing verifier."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Final

from ..discovery import resolve_repository_root
from ..fingerprint import fingerprint_repository
from ..models import (
    ContentFingerprint,
    Evidence,
    JSONValue,
    VerificationRun,
    dumps_json,
)
from .artifacts import (
    ArtifactManifest,
    artifacts_match,
    collect_artifacts,
    empty_artifact_manifest,
    normalize_artifact_paths,
)
from .executor import (
    ExecutionResult,
    VerificationStatus,
    docker_image_for_profile,
    execute_plan,
)
from .planner import PlanPhase, VerificationPlan, plan_verification
from .workspace import materialize_workspace

EXPERIMENT_SCHEMA_VERSION: Final[str] = "experiment-v1"


class ExperimentStatus(str):
    """Pair-level classification for two verification runs."""

    REPRODUCIBLE = "reproducible"
    DRIFT_DETECTED = "drift_detected"
    PASS_FAIL_DISAGREEMENT = "pass_fail_disagreement"
    BOTH_FAILED = "both_failed"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    COMPARISON_INVALID = "comparison_invalid"
    INCOMPLETE = "incomplete"


def _plan_fingerprint(plan: VerificationPlan) -> str:
    payload: dict[str, JSONValue] = {
        "profile": plan.profile,
        "ecosystem": plan.ecosystem.value,
        "commands": [
            {
                "argv": list(item.argv),
                "phase": item.phase.value,
                "network_required": item.network_required,
                "purpose": item.purpose,
            }
            for item in plan.commands
        ],
        "limitations": list(plan.limitations),
    }
    return sha256(dumps_json(payload).encode("utf-8")).hexdigest()


def _run_has_acquisition_failure(result: ExecutionResult, plan: VerificationPlan) -> bool:
    for index, command in enumerate(result.run.commands):
        if command.result_state.value == "verified_fail":
            return (
                index < len(plan.commands) and plan.commands[index].phase is PlanPhase.ACQUISITION
            )
    return False


def _run_outcome(result: ExecutionResult) -> dict[str, JSONValue]:
    return {
        "run_id": result.run.run_id,
        "status": result.status.value,
        "state": result.run.state.value,
        "commands": [
            {
                "result_state": command.result_state.value,
                "exit_code": command.exit_code,
                "timed_out": command.timed_out,
            }
            for command in result.run.commands
        ],
    }


def _classify(
    plan: VerificationPlan,
    results: tuple[ExecutionResult, ...],
    artifacts: tuple[ArtifactManifest, ...],
    *,
    approved: bool,
    source_unchanged: bool,
) -> tuple[str, dict[str, JSONValue], tuple[str, ...]]:
    limitations: set[str] = set()
    comparison: dict[str, JSONValue] = {
        "run_outcomes": [_run_outcome(result) for result in results],
        "artifact_manifests": [manifest.to_dict() for manifest in artifacts],
        "artifact_equal": False,
    }
    if not approved:
        limitations.add("Explicit approval was not granted; neither experiment run executed.")
        comparison["reason"] = "approval_required"
        return ExperimentStatus.INCOMPLETE, comparison, tuple(sorted(limitations))
    if not source_unchanged:
        limitations.add("The source repository changed during the experiment.")
        comparison["reason"] = "source_changed"
        return ExperimentStatus.COMPARISON_INVALID, comparison, tuple(sorted(limitations))
    if len(results) != 2:
        limitations.add("Two independent run results were not collected.")
        comparison["reason"] = "missing_run"
        return ExperimentStatus.INCOMPLETE, comparison, tuple(sorted(limitations))
    if not any(item.phase is PlanPhase.VERIFICATION for item in plan.commands):
        limitations.add("The selected profile planned no verification command.")
        comparison["reason"] = "no_verification_command"
        return ExperimentStatus.INCOMPLETE, comparison, tuple(sorted(limitations))

    if any(
        result.status
        in {
            VerificationStatus.BLOCKED,
            VerificationStatus.TIMEOUT,
            VerificationStatus.INFRA_ERROR,
            VerificationStatus.DENIED,
        }
        for result in results
    ) or any(_run_has_acquisition_failure(result, plan) for result in results):
        limitations.add(
            "A startup, acquisition, or infrastructure failure prevented a valid comparison."
        )
        comparison["reason"] = "infrastructure_failure"
        return ExperimentStatus.INFRASTRUCTURE_FAILURE, comparison, tuple(sorted(limitations))

    statuses = tuple(result.status for result in results)
    if statuses == (VerificationStatus.PASS, VerificationStatus.PASS):
        if len(artifacts) != 2 or not all(item.complete for item in artifacts):
            limitations.add("Selected artifacts were missing or no artifacts were selected.")
            comparison["reason"] = "artifact_evidence_missing"
            return ExperimentStatus.INCOMPLETE, comparison, tuple(sorted(limitations))
        equal = artifacts_match(artifacts[0], artifacts[1])
        comparison["artifact_equal"] = equal
        comparison["artifact_digests"] = [item.digest for item in artifacts]
        if equal:
            return ExperimentStatus.REPRODUCIBLE, comparison, tuple(sorted(limitations))
        comparison["reason"] = "artifact_drift"
        return ExperimentStatus.DRIFT_DETECTED, comparison, tuple(sorted(limitations))

    if VerificationStatus.PASS in statuses and VerificationStatus.FAIL in statuses:
        comparison["reason"] = "pass_fail_disagreement"
        return ExperimentStatus.PASS_FAIL_DISAGREEMENT, comparison, tuple(sorted(limitations))
    if statuses == (VerificationStatus.FAIL, VerificationStatus.FAIL):
        comparison["reason"] = "both_failed"
        return ExperimentStatus.BOTH_FAILED, comparison, tuple(sorted(limitations))

    limitations.add("The experiment did not produce a complete comparable outcome.")
    comparison["reason"] = "incomplete"
    return ExperimentStatus.INCOMPLETE, comparison, tuple(sorted(limitations))


@dataclass(frozen=True)
class ExperimentResult:
    """Complete result of a two-run reproducibility experiment."""

    classification: str
    profile: str
    backend: str
    plan: VerificationPlan
    source_fingerprint: ContentFingerprint
    plan_fingerprint: str
    results: tuple[ExecutionResult, ...]
    artifacts: tuple[ArtifactManifest, ...]
    comparison: dict[str, JSONValue]
    limitations: tuple[str, ...]

    @property
    def runs(self) -> tuple[VerificationRun, ...]:
        return tuple(result.run for result in self.results)

    @property
    def evidence(self) -> tuple[Evidence, ...]:
        return tuple(item for result in self.results for item in result.evidence)

    def to_receipt_details(self) -> dict[str, JSONValue]:
        return {
            "schema_version": EXPERIMENT_SCHEMA_VERSION,
            "mode": "python-two-run",
            "classification": self.classification,
            "profile": self.profile,
            "backend": self.backend,
            "image": docker_image_for_profile(self.profile) if self.backend == "docker" else None,
            "run_count": len(self.results),
            "source_fingerprint": self.source_fingerprint.to_dict(),
            "plan_fingerprint": self.plan_fingerprint,
            "artifacts": [item.to_dict() for item in self.artifacts],
            "comparison": self.comparison,
            "limitations": list(self.limitations),
        }


def run_experiment(
    repository: Path,
    *,
    profile: str | None,
    backend: str,
    approved: bool,
    artifact_paths: tuple[str, ...] = (),
    allow_host_exec: bool = False,
    executor_options: dict[str, object] | None = None,
) -> ExperimentResult:
    """Capture one source snapshot and attempt the same plan twice."""

    root = resolve_repository_root(repository)
    normalized_artifact_paths = normalize_artifact_paths(artifact_paths)
    initial_fingerprint = fingerprint_repository(root)
    if not approved:
        plan = plan_verification(root, profile=profile)
        classification, comparison, limitations = _classify(
            plan, (), (), approved=False, source_unchanged=True
        )
        return ExperimentResult(
            classification=classification,
            profile=plan.profile,
            backend=backend,
            plan=plan,
            source_fingerprint=initial_fingerprint,
            plan_fingerprint=_plan_fingerprint(plan),
            results=(),
            artifacts=(),
            comparison=comparison,
            limitations=limitations,
        )

    with materialize_workspace(root) as snapshot_path:
        snapshot_fingerprint = fingerprint_repository(snapshot_path)
        plan = plan_verification(snapshot_path, profile=profile)
        if snapshot_fingerprint != initial_fingerprint:
            classification, comparison, limitations = _classify(
                plan, (), (), approved=True, source_unchanged=False
            )
            return ExperimentResult(
                classification=classification,
                profile=plan.profile,
                backend=backend,
                plan=plan,
                source_fingerprint=snapshot_fingerprint,
                plan_fingerprint=_plan_fingerprint(plan),
                results=(),
                artifacts=(),
                comparison=comparison,
                limitations=limitations,
            )

        options = dict(executor_options or {})
        results: list[ExecutionResult] = []
        artifacts: list[ArtifactManifest] = []
        for number in (1, 2):
            observed: list[ArtifactManifest] = []

            def observe(workspace: Path, observed: list[ArtifactManifest] = observed) -> None:
                observed.append(collect_artifacts(workspace, normalized_artifact_paths))

            result = execute_plan(
                plan,
                approved=True,
                allow_host_exec=allow_host_exec,
                backend=backend,
                run_id=f"{plan.profile}-run-{number}",
                workspace_source=snapshot_path,
                workspace_observer=observe,
                **options,
            )
            results.append(result)
            artifacts.append(
                observed[0] if observed else empty_artifact_manifest(normalized_artifact_paths)
            )

        final_fingerprint = fingerprint_repository(root)
        source_unchanged = final_fingerprint == initial_fingerprint
        result_items = tuple(results)
        artifact_items = tuple(artifacts)
        classification, comparison, limitations = _classify(
            plan,
            result_items,
            artifact_items,
            approved=True,
            source_unchanged=source_unchanged,
        )
        return ExperimentResult(
            classification=classification,
            profile=plan.profile,
            backend=backend,
            plan=plan,
            source_fingerprint=snapshot_fingerprint,
            plan_fingerprint=_plan_fingerprint(plan),
            results=result_items,
            artifacts=artifact_items,
            comparison=comparison,
            limitations=limitations,
        )


__all__ = [
    "EXPERIMENT_SCHEMA_VERSION",
    "ExperimentResult",
    "ExperimentStatus",
    "run_experiment",
]
