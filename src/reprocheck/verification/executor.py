"""Approval-gated subprocess execution for verification plans."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import IO, Callable, Mapping

from ..models import (
    CommandExecution,
    CommandSpec,
    EnvironmentFingerprint,
    Evidence,
    ResultState,
    VerificationClass,
    VerificationRun,
)
from .planner import PlannedCommand, VerificationPlan
from .redaction import bounded_redact
from .workspace import WorkspaceError, materialize_workspace

DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024
DEFAULT_DOCKER_CPUS = "1"
DEFAULT_DOCKER_MEMORY = "512m"
DEFAULT_DOCKER_PIDS_LIMIT = "128"
PYTHON_DOCKER_IMAGE = "ghcr.io/astral-sh/uv:python3.12-bookworm-slim"
NODE_DOCKER_IMAGE = "node:22-slim"
DEFAULT_ENV_ALLOWLIST = frozenset(
    {
        "CI",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PATH",
        "PATHEXT",
        "PYTHONIOENCODING",
        "PYTHONUNBUFFERED",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USER",
        "USERNAME",
    }
)
_SECRET_ENV_RE = __import__("re").compile(
    r"(?i)(?:token|secret|password|passwd|api[_-]?key|private[_-]?key|credential)"
)


def docker_image_for_profile(profile: str) -> str:
    """Return the pinned default image for a supported verification profile."""

    return NODE_DOCKER_IMAGE if profile == "node-npm" else PYTHON_DOCKER_IMAGE


class VerificationStatus(str, Enum):
    """Operational status of an approved or policy-gated verification."""

    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"
    INFRA_ERROR = "infra_error"
    DENIED = "denied"


# A shorter name is useful to callers that model command execution rather than
# the complete verification run.
ExecutionStatus = VerificationStatus


def _is_allowed_name(name: str, allowlist: frozenset[str]) -> bool:
    return name in allowlist and not _SECRET_ENV_RE.search(name)


def sanitize_environment(
    source: Mapping[str, str] | None = None,
    *,
    overrides: Mapping[str, str] | None = None,
    allowlist: frozenset[str] = DEFAULT_ENV_ALLOWLIST,
) -> dict[str, str]:
    """Return only explicitly allowlisted, non-secret environment variables."""

    values = os.environ if source is None else source
    sanitized = {
        name: value
        for name, value in values.items()
        if _is_allowed_name(name, allowlist) and isinstance(value, str)
    }
    for name, value in (overrides or {}).items():
        if _is_allowed_name(name, allowlist) and isinstance(value, str):
            sanitized[name] = value
    sanitized.setdefault("PATH", os.defpath)
    return sanitized


def _omitted_environment_names(
    source: Mapping[str, str], *, allowlist: frozenset[str]
) -> tuple[str, ...]:
    return tuple(sorted(name for name in source if not _is_allowed_name(name, allowlist)))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _environment_fingerprint(
    environment: Mapping[str, str], backend: str
) -> EnvironmentFingerprint:
    digest_input = json.dumps(
        {"backend": backend, "names": sorted(environment), "values": environment},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return EnvironmentFingerprint(
        os_name=platform.system(),
        architecture=platform.machine(),
        os_version=platform.platform(),
        python_version=platform.python_version(),
        runtime_versions={"python": platform.python_version()},
        tool_versions={"backend": backend},
        environment_digest=hashlib.sha256(digest_input).hexdigest(),
    )


def _resource_preexec(
    *,
    cpu_seconds: int | None,
    address_space_bytes: int | None,
) -> tuple[Callable[[], None] | None, tuple[str, ...]]:
    if os.name == "nt":
        return None, ("Process resource limits are unavailable on this platform.",)
    try:
        import resource
    except ImportError:
        return None, ("Process resource limits are unavailable on this platform.",)

    def limit_resources() -> None:
        if cpu_seconds is not None and hasattr(resource, "RLIMIT_CPU"):
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        if address_space_bytes is not None and hasattr(resource, "RLIMIT_AS"):
            resource.setrlimit(resource.RLIMIT_AS, (address_space_bytes, address_space_bytes))

    return limit_resources, ()


class _OutputCollector:
    def __init__(self, stream: IO[bytes], maximum: int) -> None:
        self._stream = stream
        self._maximum = maximum
        self._chunks: list[bytes] = []
        self._size = 0
        self._thread = threading.Thread(target=self._collect, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _collect(self) -> None:
        stream = self._stream
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            if self._size < self._maximum:
                remaining = self._maximum - self._size
                self._chunks.append(chunk[:remaining])
                self._size += min(len(chunk), remaining)

    def join(self) -> None:
        self._thread.join(timeout=5.0)

    def value(self) -> str:
        return b"".join(self._chunks).decode("utf-8", errors="replace")


@dataclass(frozen=True)
class _ProcessOutcome:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    error: str | None = None


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        try:
            process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
            process.wait(timeout=0.5)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
        return

    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            process.terminate()
        except OSError:
            pass
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass


def _run_argv(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: float | None,
    maximum_output_bytes: int,
    cpu_seconds: int | None,
    address_space_bytes: int | None,
) -> _ProcessOutcome:
    if not argv or any(not isinstance(item, str) or not item for item in argv):
        return _ProcessOutcome(None, "", "", False, "command argv must contain non-empty strings")
    preexec, _ = _resource_preexec(
        cpu_seconds=cpu_seconds,
        address_space_bytes=address_space_bytes,
    )
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=os.name != "nt",
            creationflags=creationflags,
            preexec_fn=preexec,
        )
    except (OSError, ValueError) as error:
        return _ProcessOutcome(None, "", "", False, bounded_redact(str(error)))

    if process.stdout is None or process.stderr is None:  # pragma: no cover - Popen contract guard
        process.kill()
        process.wait()
        return _ProcessOutcome(None, "", "", False, "process output pipes were unavailable")
    stdout = _OutputCollector(process.stdout, maximum_output_bytes)
    stderr = _OutputCollector(process.stderr, maximum_output_bytes)
    stdout.start()
    stderr.start()
    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_group(process)
    stdout.join()
    stderr.join()
    return _ProcessOutcome(
        process.returncode,
        stdout.value(),
        stderr.value(),
        timed_out,
    )


def _safe_cwd(workspace: Path, command: CommandSpec) -> Path:
    relative_value = command.cwd or "."
    relative = PurePosixPath(relative_value)
    if (
        relative.is_absolute()
        or any(part in {"", ".."} for part in relative.parts)
        or relative.as_posix() != relative_value
    ):
        raise WorkspaceError(f"command cwd must be workspace-relative: {relative_value!r}")
    candidate = workspace.joinpath(*relative.parts)
    try:
        candidate.resolve(strict=True).relative_to(workspace.resolve(strict=True))
    except (OSError, RuntimeError, ValueError) as error:
        raise WorkspaceError(f"command cwd escapes workspace: {relative_value!r}") from error
    if not candidate.is_dir():
        raise WorkspaceError(f"command cwd is not a directory: {relative_value!r}")
    return candidate


def _phase_class(planned: PlannedCommand) -> VerificationClass:
    if planned.purpose.lower().find("test") >= 0:
        return VerificationClass.TEST
    if planned.purpose.lower().find("build") >= 0:
        return VerificationClass.BUILD
    return VerificationClass.COMMAND


@dataclass(frozen=True)
class ExecutionResult:
    """Complete result of one plan execution or policy decision."""

    status: VerificationStatus
    profile: str
    run: VerificationRun
    evidence: tuple[Evidence, ...]
    environment: EnvironmentFingerprint
    limitations: tuple[str, ...] = ()

    @property
    def commands(self) -> tuple[CommandExecution, ...]:
        return self.run.commands


class SubprocessExecutor:
    """Execute plans with an explicit approval and backend policy gate."""

    def __init__(
        self,
        *,
        backend: str = "host",
        allow_host_exec: bool = False,
        docker_executable: str = "docker",
        docker_image: str | None = None,
        docker_cpus: str = DEFAULT_DOCKER_CPUS,
        docker_memory: str = DEFAULT_DOCKER_MEMORY,
        docker_pids_limit: str = DEFAULT_DOCKER_PIDS_LIMIT,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        default_timeout_seconds: float = 600.0,
        cpu_seconds: int | None = 120,
        address_space_bytes: int | None = None,
        environment_allowlist: frozenset[str] = DEFAULT_ENV_ALLOWLIST,
    ) -> None:
        if backend not in {"host", "docker"}:
            raise ValueError("backend must be 'host' or 'docker'")
        if max_output_bytes < 0:
            raise ValueError("max_output_bytes must be non-negative")
        self.backend = backend
        self.allow_host_exec = allow_host_exec
        self.docker_executable = docker_executable
        self.docker_image = docker_image
        self.docker_cpus = docker_cpus
        self.docker_memory = docker_memory
        self.docker_pids_limit = docker_pids_limit
        self.max_output_bytes = max_output_bytes
        self.default_timeout_seconds = default_timeout_seconds
        self.cpu_seconds = cpu_seconds
        self.address_space_bytes = address_space_bytes
        self.environment_allowlist = environment_allowlist

    @property
    def docker_available(self) -> bool:
        return shutil.which(self.docker_executable) is not None

    def _environment(self, command: CommandSpec) -> tuple[dict[str, str], tuple[str, ...]]:
        explicit = dict(command.env)
        environment = sanitize_environment(
            overrides=explicit,
            allowlist=self.environment_allowlist,
        )
        omitted = _omitted_environment_names(
            {**dict(os.environ), **explicit},
            allowlist=self.environment_allowlist,
        )
        limitations: tuple[str, ...] = ()
        if omitted:
            limitations = (
                "Environment variables outside the allowlist were omitted: " + ", ".join(omitted),
            )
        return environment, limitations

    def _docker_argv(
        self,
        planned: PlannedCommand,
        *,
        workspace: Path,
        environment: Mapping[str, str],
        image: str,
    ) -> tuple[str, ...]:
        network = "bridge" if planned.network_required else "none"
        env_args: list[str] = []
        for name in sorted(environment):
            env_args.extend(("--env", f"{name}={environment[name]}"))
        return (
            self.docker_executable,
            "run",
            "--rm",
            "--network",
            network,
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--cpus",
            self.docker_cpus,
            "--memory",
            self.docker_memory,
            "--pids-limit",
            self.docker_pids_limit,
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--mount",
            f"type=bind,src={workspace},dst=/workspace",
            "--workdir",
            "/workspace",
            *env_args,
            image,
            *planned.argv,
        )

    def _control_result(
        self,
        plan: VerificationPlan,
        status: VerificationStatus,
        message: str,
        *,
        environment: EnvironmentFingerprint,
        limitations: tuple[str, ...] = (),
    ) -> ExecutionResult:
        observed = _now()
        run = VerificationRun(
            run_id=f"{plan.profile}-run",
            verification_class=VerificationClass.COMMAND,
            state=ResultState.NOT_VERIFIED,
            environment=environment,
            started_at=observed,
            finished_at=observed,
            error=message,
        )
        return ExecutionResult(status, plan.profile, run, (), environment, limitations)

    def execute(self, plan: VerificationPlan, *, approved: bool | None = None) -> ExecutionResult:
        """Execute a plan only when approval and backend policy both permit it."""

        if approved is not None and not isinstance(approved, bool):
            raise TypeError("approved must be an explicit boolean")
        safe_environment = sanitize_environment(allowlist=self.environment_allowlist)
        environment = _environment_fingerprint(safe_environment, self.backend)
        if approved is not True:
            return self._control_result(
                plan,
                VerificationStatus.DENIED,
                "Explicit approval boolean was not granted; no command was executed.",
                environment=environment,
            )
        if self.backend == "host" and not self.allow_host_exec:
            return self._control_result(
                plan,
                VerificationStatus.BLOCKED,
                "Host execution requires allow_host_exec=True.",
                environment=environment,
                limitations=(
                    "Host execution was blocked because the explicit allow_host_exec policy "
                    "was absent.",
                ),
            )
        if self.backend == "docker" and not self.docker_available:
            return self._control_result(
                plan,
                VerificationStatus.BLOCKED,
                "Docker backend is unavailable because its executable was not found.",
                environment=environment,
                limitations=(
                    "Docker sandbox backend is unavailable; sandbox completeness is not claimed.",
                ),
            )

        limitations: set[str] = set(plan.limitations)
        if self.backend == "host":
            limitations.add(
                "Host execution is not a complete sandbox; repository commands may affect the host."
            )
        else:
            limitations.add(
                "Docker execution is backend-isolated, but sandbox completeness is not claimed."
            )
        _, resource_limitations = _resource_preexec(
            cpu_seconds=self.cpu_seconds,
            address_space_bytes=self.address_space_bytes,
        )
        limitations.update(resource_limitations)

        started = _now()
        command_executions: list[CommandExecution] = []
        evidence: list[Evidence] = []
        run_error: str | None = None
        overall_status = VerificationStatus.PASS
        try:
            with materialize_workspace(plan.repository) as workspace:
                for index, planned in enumerate(plan.commands):
                    command = planned.command
                    command_environment, env_limitations = self._environment(command)
                    limitations.update(env_limitations)
                    safe_command = CommandSpec(
                        executable=command.executable,
                        args=command.args,
                        cwd=command.cwd,
                        env=command_environment,
                        timeout_seconds=command.timeout_seconds,
                        description=command.description,
                    )
                    try:
                        cwd = _safe_cwd(workspace, command)
                        argv = planned.argv
                        if self.backend == "docker":
                            image = self.docker_image or (docker_image_for_profile(plan.profile))
                            argv = self._docker_argv(
                                planned,
                                workspace=workspace,
                                environment=command_environment,
                                image=image,
                            )
                            cwd = workspace
                        timeout = command.timeout_seconds or self.default_timeout_seconds
                        if timeout <= 0:
                            raise ValueError("command timeout must be positive")
                        command_started = _now()
                        monotonic_started = time.monotonic()
                        process_outcome = _run_argv(
                            argv,
                            cwd=cwd,
                            environment=command_environment,
                            timeout_seconds=timeout,
                            maximum_output_bytes=self.max_output_bytes,
                            cpu_seconds=self.cpu_seconds,
                            address_space_bytes=self.address_space_bytes,
                        )
                        duration_ms = int((time.monotonic() - monotonic_started) * 1000)
                        finished = _now()
                        if process_outcome.timed_out:
                            command_status = VerificationStatus.TIMEOUT
                            result_state = ResultState.NOT_VERIFIED
                        elif process_outcome.error is not None:
                            command_status = VerificationStatus.INFRA_ERROR
                            result_state = ResultState.NOT_VERIFIED
                        elif process_outcome.exit_code == 0:
                            command_status = VerificationStatus.PASS
                            result_state = ResultState.VERIFIED_PASS
                        else:
                            command_status = VerificationStatus.FAIL
                            result_state = ResultState.VERIFIED_FAIL
                        safe_command_execution = CommandExecution(
                            command=safe_command,
                            result_state=result_state,
                            exit_code=process_outcome.exit_code,
                            stdout=bounded_redact(process_outcome.stdout),
                            stderr=bounded_redact(
                                process_outcome.stderr
                                + (f"\n{process_outcome.error}" if process_outcome.error else "")
                            ),
                            started_at=command_started,
                            finished_at=finished,
                            duration_ms=duration_ms,
                            timed_out=process_outcome.timed_out,
                        )
                    except (OSError, ValueError, WorkspaceError) as error:
                        command_status = VerificationStatus.INFRA_ERROR
                        run_error = bounded_redact(str(error))
                        safe_command_execution = CommandExecution(
                            command=safe_command,
                            result_state=ResultState.NOT_VERIFIED,
                            stderr=run_error,
                            started_at=_now(),
                            finished_at=_now(),
                        )

                    command_executions.append(safe_command_execution)
                    evidence_id = f"{plan.profile}-command-{index + 1}"
                    evidence.append(
                        Evidence(
                            id=evidence_id,
                            verification_class=_phase_class(planned),
                            summary=f"{planned.purpose}: {command_status.value}",
                            details={
                                "argv": list(planned.argv),
                                "phase": planned.phase.value,
                                "network_required": planned.network_required,
                                "status": command_status.value,
                            },
                            command=safe_command_execution,
                            observed_at=safe_command_execution.finished_at,
                        )
                    )
                    if command_status is not VerificationStatus.PASS:
                        overall_status = command_status
                        limitations.add(
                            "Verification stopped after "
                            f"{planned.purpose} returned {command_status.value}."
                        )
                        break
        except (OSError, RuntimeError, WorkspaceError) as error:
            overall_status = VerificationStatus.INFRA_ERROR
            run_error = bounded_redact(str(error))

        finished = _now()
        if overall_status is VerificationStatus.PASS:
            run_state = ResultState.VERIFIED_PASS
        elif overall_status is VerificationStatus.FAIL:
            run_state = ResultState.VERIFIED_FAIL
        else:
            run_state = ResultState.NOT_VERIFIED
        run = VerificationRun(
            run_id=f"{plan.profile}-run",
            verification_class=VerificationClass.COMMAND,
            state=run_state,
            commands=tuple(command_executions),
            environment=environment,
            evidence_ids=tuple(item.id for item in evidence),
            started_at=started,
            finished_at=finished,
            error=run_error,
        )
        return ExecutionResult(
            status=overall_status,
            profile=plan.profile,
            run=run,
            evidence=tuple(evidence),
            environment=environment,
            limitations=tuple(sorted(limitations)),
        )


class HostSubprocessExecutor(SubprocessExecutor):
    """Convenience host backend that still requires approval at execution time."""

    def __init__(self, *, allow_host_exec: bool = False, **kwargs: object) -> None:
        super().__init__(backend="host", allow_host_exec=allow_host_exec, **kwargs)  # type: ignore[arg-type]


class DockerSubprocessExecutor(SubprocessExecutor):
    """Docker backend that reports unavailable instead of falling back to host."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(backend="docker", **kwargs)  # type: ignore[arg-type]


DockerExecutor = DockerSubprocessExecutor
VerificationExecutor = SubprocessExecutor


def execute_plan(
    plan: VerificationPlan,
    *,
    approved: bool | None = None,
    allow_host_exec: bool = False,
    backend: str = "host",
    **executor_options: object,
) -> ExecutionResult:
    """Convenience function for one explicit verification execution."""

    executor = SubprocessExecutor(
        backend=backend,
        allow_host_exec=allow_host_exec,
        **executor_options,  # type: ignore[arg-type]
    )
    return executor.execute(plan, approved=approved)


execute_verification = execute_plan


__all__ = [
    "DEFAULT_ENV_ALLOWLIST",
    "DEFAULT_DOCKER_CPUS",
    "DEFAULT_DOCKER_MEMORY",
    "DEFAULT_DOCKER_PIDS_LIMIT",
    "NODE_DOCKER_IMAGE",
    "PYTHON_DOCKER_IMAGE",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "ExecutionResult",
    "ExecutionStatus",
    "DockerExecutor",
    "DockerSubprocessExecutor",
    "HostSubprocessExecutor",
    "SubprocessExecutor",
    "VerificationExecutor",
    "VerificationStatus",
    "execute_plan",
    "execute_verification",
    "sanitize_environment",
    "docker_image_for_profile",
]
