"""Explicit, approval-gated verification orchestration."""

from .executor import (
    DEFAULT_ENV_ALLOWLIST,
    DEFAULT_MAX_OUTPUT_BYTES,
    DockerExecutor,
    DockerSubprocessExecutor,
    ExecutionResult,
    ExecutionStatus,
    HostSubprocessExecutor,
    SubprocessExecutor,
    VerificationExecutor,
    VerificationStatus,
    execute_plan,
    execute_verification,
    sanitize_environment,
)
from .planner import (
    AcquisitionPolicy,
    PlannedCommand,
    PlanPhase,
    ProfileError,
    VerificationPlan,
    available_profiles,
    plan_verification,
)
from .workspace import MaterializedWorkspace, WorkspaceError, materialize_workspace

__all__ = [
    "AcquisitionPolicy",
    "DEFAULT_ENV_ALLOWLIST",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DockerExecutor",
    "DockerSubprocessExecutor",
    "ExecutionResult",
    "ExecutionStatus",
    "HostSubprocessExecutor",
    "MaterializedWorkspace",
    "PlanPhase",
    "PlannedCommand",
    "ProfileError",
    "SubprocessExecutor",
    "VerificationExecutor",
    "VerificationPlan",
    "VerificationStatus",
    "WorkspaceError",
    "available_profiles",
    "execute_plan",
    "execute_verification",
    "materialize_workspace",
    "plan_verification",
    "sanitize_environment",
]
