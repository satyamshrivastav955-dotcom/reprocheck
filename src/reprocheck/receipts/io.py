"""JSON loading for the versioned evidence receipt contract."""

from __future__ import annotations

import json
import math
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


def _reject_json_constant(constant: str) -> Any:
    raise ValueError(f"non-standard JSON number: {constant}")


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
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ReceiptFormatError(f"receipt field must be an object: {field}")
    return value


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise ReceiptFormatError(f"receipt field must be a boolean: {field}")
    return value


def _optional_boolean(value: Any, field: str) -> bool | None:
    if value is not None and type(value) is not bool:
        raise ReceiptFormatError(f"receipt field must be a boolean or null: {field}")
    return value


def _optional_integer(value: Any, field: str) -> int | None:
    if value is not None and type(value) is not int:
        raise ReceiptFormatError(f"receipt field must be an integer or null: {field}")
    return value


def _optional_number(value: Any, field: str) -> int | float | None:
    if value is None:
        return None
    if type(value) not in (int, float):
        raise ReceiptFormatError(f"receipt field must be a finite number or null: {field}")
    if type(value) is float and not math.isfinite(value):
        raise ReceiptFormatError(f"receipt field must be a finite number or null: {field}")
    if type(value) is int:
        try:
            float(value)
        except OverflowError as error:
            raise ReceiptFormatError(
                f"receipt field must be a finite number or null: {field}"
            ) from error
    return int(value) if type(value) is int else float(value)


def _result_state(value: Any, field: str) -> ResultState:
    raw = _string(value, field)
    try:
        return ResultState(raw)
    except ValueError as error:
        raise ReceiptFormatError(f"receipt field has an unsupported value: {field}") from error


def _verification_class(value: Any, field: str) -> VerificationClass:
    raw = _string(value, field)
    try:
        return VerificationClass(raw)
    except ValueError as error:
        raise ReceiptFormatError(f"receipt field has an unsupported value: {field}") from error


def _content_fingerprint(value: Any, field: str) -> ContentFingerprint:
    data = _mapping(value, field)
    return ContentFingerprint(
        algorithm=_string(_required(data, "algorithm"), f"{field}.algorithm"),
        digest=_string(_required(data, "digest"), f"{field}.digest"),
        file_count=_optional_integer(_required(data, "file_count"), f"{field}.file_count"),
    )


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
    timeout = _optional_number(_required(data, "timeout_seconds"), "command.timeout_seconds")
    return CommandSpec(
        executable=_string(_required(data, "executable"), "command.executable"),
        args=tuple(args),
        cwd=_optional_string(_required(data, "cwd"), "command.cwd"),
        env=env,
        timeout_seconds=float(timeout) if timeout is not None else None,
        description=_optional_string(_required(data, "description"), "command.description"),
    )


def _command_execution(value: Any) -> CommandExecution:
    data = _mapping(value, "command execution")
    exit_code = _optional_integer(_required(data, "exit_code"), "command execution.exit_code")
    duration = _optional_integer(_required(data, "duration_ms"), "command execution.duration_ms")
    return CommandExecution(
        command=_command_spec(_required(data, "command")),
        result_state=_result_state(_required(data, "result_state"), "result_state"),
        exit_code=exit_code,
        stdout=_string(_required(data, "stdout"), "stdout"),
        stderr=_string(_required(data, "stderr"), "stderr"),
        started_at=_optional_string(_required(data, "started_at"), "started_at"),
        finished_at=_optional_string(_required(data, "finished_at"), "finished_at"),
        duration_ms=duration,
        timed_out=_boolean(_required(data, "timed_out"), "timed_out"),
    )


def _environment(value: Any) -> EnvironmentFingerprint:
    data = _mapping(value, "environment")
    runtime = _mapping(_required(data, "runtime_versions"), "runtime_versions")
    tools = _mapping(_required(data, "tool_versions"), "tool_versions")
    return EnvironmentFingerprint(
        os_name=_string(_required(data, "os_name"), "os_name"),
        architecture=_string(_required(data, "architecture"), "architecture"),
        os_version=_optional_string(_required(data, "os_version"), "os_version"),
        python_version=_optional_string(_required(data, "python_version"), "python_version"),
        runtime_versions={
            key: _string(item, f"environment.runtime_versions.{key}")
            for key, item in runtime.items()
        },
        tool_versions={
            key: _string(item, f"environment.tool_versions.{key}") for key, item in tools.items()
        },
        environment_digest=_optional_string(
            _required(data, "environment_digest"), "environment_digest"
        ),
    )


def _evidence(value: Any) -> Evidence:
    data = _mapping(value, "evidence")
    details = _mapping(_required(data, "details"), "evidence.details")
    command = _required(data, "command")
    return Evidence(
        id=_string(_required(data, "id"), "evidence.id"),
        verification_class=_verification_class(
            _required(data, "verification_class"), "verification_class"
        ),
        summary=_string(_required(data, "summary"), "evidence.summary"),
        check_id=_optional_string(_required(data, "check_id"), "evidence.check_id"),
        details=details,
        source=_optional_string(_required(data, "source"), "evidence.source"),
        command=_command_execution(command) if command is not None else None,
        observed_at=_optional_string(_required(data, "observed_at"), "evidence.observed_at"),
    )


def _run(value: Any) -> VerificationRun:
    data = _mapping(value, "verification run")
    commands = _required(data, "commands")
    evidence_ids = _required(data, "evidence_ids")
    if not isinstance(commands, list) or not isinstance(evidence_ids, list):
        raise ReceiptFormatError("verification run commands and evidence_ids must be arrays")
    environment = _required(data, "environment")
    return VerificationRun(
        run_id=_string(_required(data, "run_id"), "run_id"),
        verification_class=_verification_class(
            _required(data, "verification_class"), "verification_class"
        ),
        state=_result_state(_required(data, "state"), "state"),
        commands=tuple(_command_execution(item) for item in commands),
        environment=_environment(environment) if environment is not None else None,
        evidence_ids=tuple(_string(item, "evidence_id") for item in evidence_ids),
        started_at=_optional_string(_required(data, "started_at"), "started_at"),
        finished_at=_optional_string(_required(data, "finished_at"), "finished_at"),
        error=_optional_string(_required(data, "error"), "error"),
    )


def receipt_from_dict(value: object) -> EvidenceReceipt:
    """Parse a JSON object into the supported receipt model."""

    data = _mapping(value, "receipt")
    if _string(_required(data, "schema_version"), "schema_version") != "receipt-v1":
        raise ReceiptFormatError("unsupported or missing receipt schema_version")
    repository_data = _mapping(_required(data, "repository"), "repository")
    content_data = _mapping(_required(data, "content"), "content")
    integrity_data = _mapping(_required(data, "integrity"), "integrity")
    content = _content_fingerprint(content_data, "content")
    integrity_content = _required(integrity_data, "content_fingerprint")
    integrity_details = _mapping(_required(integrity_data, "details"), "integrity.details")
    canonical_hash = integrity_details.get("canonical_sha256")
    if (
        not isinstance(canonical_hash, str)
        or len(canonical_hash) != 64
        or any(character not in "0123456789abcdef" for character in canonical_hash)
    ):
        raise ReceiptFormatError(
            "integrity.details.canonical_sha256 must be lowercase hexadecimal SHA-256"
        )
    integrity = IntegrityMetadata(
        verified=_boolean(_required(integrity_data, "verified"), "integrity.verified"),
        content_fingerprint=(
            _content_fingerprint(integrity_content, "integrity.content_fingerprint")
            if integrity_content is not None
            else None
        ),
        method=_optional_string(_required(integrity_data, "method"), "integrity.method"),
        details=integrity_details,
    )
    evidence = _required(data, "evidence")
    runs = _required(data, "verification_runs")
    if not isinstance(evidence, list) or not isinstance(runs, list):
        raise ReceiptFormatError("receipt evidence and verification_runs must be arrays")
    return EvidenceReceipt(
        receipt_id=_string(_required(data, "receipt_id"), "receipt_id"),
        repository=RepositoryIdentity(
            root=_string(_required(repository_data, "root"), "repository.root"),
            repository_id=_optional_string(
                _required(repository_data, "repository_id"), "repository_id"
            ),
            revision=_optional_string(
                _required(repository_data, "revision"), "repository.revision"
            ),
            branch=_optional_string(_required(repository_data, "branch"), "repository.branch"),
            remote_url=_optional_string(
                _required(repository_data, "remote_url"), "repository.remote_url"
            ),
            is_dirty=_optional_boolean(
                _required(repository_data, "is_dirty"), "repository.is_dirty"
            ),
        ),
        content=content,
        integrity=integrity,
        evidence=tuple(_evidence(item) for item in evidence),
        verification_runs=tuple(_run(item) for item in runs),
        generated_at=_optional_string(_required(data, "generated_at"), "generated_at"),
    )


def load_receipt(path: Path) -> EvidenceReceipt:
    """Load and parse one receipt JSON file without executing repository code."""

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ReceiptFormatError(f"could not read receipt {path}: {error}") from error
    return receipt_from_dict(value)


__all__ = ["ReceiptFormatError", "load_receipt", "receipt_from_dict"]
