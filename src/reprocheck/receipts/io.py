"""JSON loading for the versioned evidence receipt contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import (
    CommandExecution,
    CommandSpec,
    ContentFingerprint,
    EnvironmentFingerprint,
    Evidence,
    EvidenceReceipt,
    IntegrityMetadata,
    RepositoryIdentity,
    ResultState,
    VerificationClass,
    VerificationRun,
)


class ReceiptFormatError(ValueError):
    """Raised when a receipt cannot be loaded as the supported schema."""


def _required(value: dict[str, Any], name: str) -> Any:
    if name not in value:
        raise ReceiptFormatError(f"receipt field is missing: {name}")
    return value[name]


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ReceiptFormatError(f"receipt field must be a string: {field}")
    return value


def _optional_string(value: Any, field: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ReceiptFormatError(f"receipt field must be a string or null: {field}")
    return value


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReceiptFormatError(f"receipt field must be an object: {field}")
    return value


def _command_spec(value: Any) -> CommandSpec:
    data = _mapping(value, "command")
    args = _required(data, "args")
    env = _required(data, "env")
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise ReceiptFormatError("command args must be a string array")
    if not isinstance(env, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in env.items()
    ):
        raise ReceiptFormatError("command env must be a string object")
    timeout = data.get("timeout_seconds")
    if timeout is not None and not isinstance(timeout, (int, float)):
        raise ReceiptFormatError("command timeout_seconds must be numeric or null")
    return CommandSpec(
        executable=_string(_required(data, "executable"), "command.executable"),
        args=tuple(args),
        cwd=_optional_string(data.get("cwd"), "command.cwd"),
        env=env,
        timeout_seconds=float(timeout) if timeout is not None else None,
        description=_optional_string(data.get("description"), "command.description"),
    )


def _command_execution(value: Any) -> CommandExecution:
    data = _mapping(value, "command execution")
    exit_code = data.get("exit_code")
    if exit_code is not None and not isinstance(exit_code, int):
        raise ReceiptFormatError("command exit_code must be an integer or null")
    duration = data.get("duration_ms")
    if duration is not None and not isinstance(duration, int):
        raise ReceiptFormatError("command duration_ms must be an integer or null")
    return CommandExecution(
        command=_command_spec(_required(data, "command")),
        result_state=ResultState(_string(_required(data, "result_state"), "result_state")),
        exit_code=exit_code,
        stdout=_string(_required(data, "stdout"), "stdout"),
        stderr=_string(_required(data, "stderr"), "stderr"),
        started_at=_optional_string(data.get("started_at"), "started_at"),
        finished_at=_optional_string(data.get("finished_at"), "finished_at"),
        duration_ms=duration,
        timed_out=bool(_required(data, "timed_out")),
    )


def _environment(value: Any) -> EnvironmentFingerprint:
    data = _mapping(value, "environment")
    runtime = _mapping(_required(data, "runtime_versions"), "runtime_versions")
    tools = _mapping(_required(data, "tool_versions"), "tool_versions")
    return EnvironmentFingerprint(
        os_name=_string(_required(data, "os_name"), "os_name"),
        architecture=_string(_required(data, "architecture"), "architecture"),
        os_version=_optional_string(data.get("os_version"), "os_version"),
        python_version=_optional_string(data.get("python_version"), "python_version"),
        runtime_versions={
            str(key): _string(item, "runtime_versions") for key, item in runtime.items()
        },
        tool_versions={str(key): _string(item, "tool_versions") for key, item in tools.items()},
        environment_digest=_optional_string(data.get("environment_digest"), "environment_digest"),
    )


def _evidence(value: Any) -> Evidence:
    data = _mapping(value, "evidence")
    details = _mapping(data.get("details", {}), "evidence.details")
    command = data.get("command")
    return Evidence(
        id=_string(_required(data, "id"), "evidence.id"),
        verification_class=VerificationClass(
            _string(_required(data, "verification_class"), "verification_class")
        ),
        summary=_string(_required(data, "summary"), "evidence.summary"),
        check_id=_optional_string(data.get("check_id"), "evidence.check_id"),
        details=details,
        source=_optional_string(data.get("source"), "evidence.source"),
        command=_command_execution(command) if command is not None else None,
        observed_at=_optional_string(data.get("observed_at"), "evidence.observed_at"),
    )


def _run(value: Any) -> VerificationRun:
    data = _mapping(value, "verification run")
    commands = _required(data, "commands")
    evidence_ids = _required(data, "evidence_ids")
    if not isinstance(commands, list) or not isinstance(evidence_ids, list):
        raise ReceiptFormatError("verification run commands and evidence_ids must be arrays")
    return VerificationRun(
        run_id=_string(_required(data, "run_id"), "run_id"),
        verification_class=VerificationClass(
            _string(_required(data, "verification_class"), "verification_class")
        ),
        state=ResultState(_string(_required(data, "state"), "state")),
        commands=tuple(_command_execution(item) for item in commands),
        environment=_environment(data["environment"])
        if data.get("environment") is not None
        else None,
        evidence_ids=tuple(_string(item, "evidence_id") for item in evidence_ids),
        started_at=_optional_string(data.get("started_at"), "started_at"),
        finished_at=_optional_string(data.get("finished_at"), "finished_at"),
        error=_optional_string(data.get("error"), "error"),
    )


def receipt_from_dict(value: object) -> EvidenceReceipt:
    """Parse a JSON object into the supported receipt model."""

    data = _mapping(value, "receipt")
    if data.get("schema_version") != "receipt-v1":
        raise ReceiptFormatError("unsupported or missing receipt schema_version")
    repository_data = _mapping(_required(data, "repository"), "repository")
    content_data = _mapping(_required(data, "content"), "content")
    integrity_data = _mapping(_required(data, "integrity"), "integrity")
    content = ContentFingerprint(
        algorithm=_string(_required(content_data, "algorithm"), "content.algorithm"),
        digest=_string(_required(content_data, "digest"), "content.digest"),
        file_count=content_data.get("file_count"),
    )
    integrity_content = integrity_data.get("content_fingerprint")
    integrity = IntegrityMetadata(
        verified=bool(_required(integrity_data, "verified")),
        content_fingerprint=(
            ContentFingerprint(
                algorithm=_string(_required(integrity_content, "algorithm"), "integrity.algorithm"),
                digest=_string(_required(integrity_content, "digest"), "integrity.digest"),
                file_count=integrity_content.get("file_count"),
            )
            if integrity_content is not None
            else None
        ),
        method=_optional_string(integrity_data.get("method"), "integrity.method"),
        details=_mapping(integrity_data.get("details", {}), "integrity.details"),
    )
    evidence = _required(data, "evidence")
    runs = _required(data, "verification_runs")
    if not isinstance(evidence, list) or not isinstance(runs, list):
        raise ReceiptFormatError("receipt evidence and verification_runs must be arrays")
    return EvidenceReceipt(
        receipt_id=_string(_required(data, "receipt_id"), "receipt_id"),
        repository=RepositoryIdentity(
            root=_string(_required(repository_data, "root"), "repository.root"),
            repository_id=_optional_string(repository_data.get("repository_id"), "repository_id"),
            revision=_optional_string(repository_data.get("revision"), "repository.revision"),
            branch=_optional_string(repository_data.get("branch"), "repository.branch"),
            remote_url=_optional_string(repository_data.get("remote_url"), "repository.remote_url"),
            is_dirty=repository_data.get("is_dirty"),
        ),
        content=content,
        integrity=integrity,
        evidence=tuple(_evidence(item) for item in evidence),
        verification_runs=tuple(_run(item) for item in runs),
        generated_at=_optional_string(data.get("generated_at"), "generated_at"),
    )


def load_receipt(path: Path) -> EvidenceReceipt:
    """Load and parse one receipt JSON file without executing repository code."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReceiptFormatError(f"could not read receipt {path}: {error}") from error
    return receipt_from_dict(value)


__all__ = ["ReceiptFormatError", "load_receipt", "receipt_from_dict"]
