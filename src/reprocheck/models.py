"""Small, dependency-free domain contracts used by ReproCheck.

The models intentionally contain data only.  Scanning, check execution, and
verification orchestration belong to callers of this module.  All timestamps
are represented as strings so integrations can choose their preferred
RFC 3339 representation without introducing a datetime serialization policy.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from hashlib import sha256
from typing import Any, Dict, Optional, Set, Tuple, Union

JSONPrimitive = Union[None, bool, int, float, str]
JSONValue = Union[JSONPrimitive, list["JSONValue"], dict[str, "JSONValue"]]


class ResultState(str, Enum):
    """Lifecycle/result state for a discovered or verified check."""

    PRESENT = "present"
    LOOKS_CORRECT = "looks_correct"
    STATIC_FAIL = "static_fail"
    VERIFIED_PASS = "verified_pass"
    VERIFIED_FAIL = "verified_fail"
    NOT_VERIFIED = "not_verified"


class Assessment(str, Enum):
    """Human-facing assessment associated with a finding or report."""

    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    INFO = "info"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"
    UNSUPPORTED = "unsupported"
    BLOCKED = "blocked"
    ERROR = "error"


class Severity(str, Enum):
    """Impact level of a finding."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Ecosystem(str, Enum):
    """Supported or detected software ecosystem."""

    PYTHON = "python"
    NODE = "node"
    GO = "go"
    RUST = "rust"
    JAVA = "java"
    DOTNET = "dotnet"
    RUBY = "ruby"
    PHP = "php"
    ELIXIR = "elixir"
    CONTAINER = "container"
    UNKNOWN = "unknown"


class CheckCategory(str, Enum):
    """Broad area addressed by a check."""

    REPOSITORY = "repository"
    RUNTIME = "runtime"
    TOOLCHAIN = "toolchain"
    DEPENDENCIES = "dependencies"
    TESTS = "tests"
    BUILD = "build"
    CONTAINER = "container"
    INTEGRITY = "integrity"
    DOCUMENTATION = "documentation"


class VerificationClass(str, Enum):
    """Kind of evidence a check can produce."""

    STATIC = "static"
    COMMAND = "command"
    TEST = "test"
    BUILD = "build"
    ENVIRONMENT = "environment"
    INTEGRITY = "integrity"


class JsonModel:
    """Mixin providing JSON-safe conversion for domain dataclasses."""

    def to_dict(self) -> Dict[str, JSONValue]:
        """Return this model as a JSON-compatible dictionary."""

        value = to_json_dict(self)
        if not isinstance(value, dict):  # pragma: no cover - protects the type contract
            raise TypeError("a model must serialize to an object")
        return value

    def to_json(self, *, indent: Optional[int] = None) -> str:
        """Return deterministic JSON for this model."""

        return dumps_json(self, indent=indent)


@dataclass(frozen=True)
class CommandSpec(JsonModel):
    """A command that a check may ask an executor to run."""

    executable: str
    args: Tuple[str, ...] = ()
    cwd: Optional[str] = None
    env: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: Optional[float] = None
    description: Optional[str] = None


@dataclass(frozen=True)
class CommandExecution(JsonModel):
    """Observed result of running a :class:`CommandSpec`."""

    command: CommandSpec
    result_state: ResultState = ResultState.NOT_VERIFIED
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_ms: Optional[int] = None
    timed_out: bool = False

    @property
    def succeeded(self) -> bool:
        """Whether the execution completed successfully with exit code zero."""

        return self.exit_code == 0 and not self.timed_out


@dataclass(frozen=True)
class EnvironmentFingerprint(JsonModel):
    """Non-secret description of the environment used for verification."""

    os_name: str
    architecture: str
    os_version: Optional[str] = None
    python_version: Optional[str] = None
    runtime_versions: Mapping[str, str] = field(default_factory=dict)
    tool_versions: Mapping[str, str] = field(default_factory=dict)
    environment_digest: Optional[str] = None


@dataclass(frozen=True)
class Evidence(JsonModel):
    """One auditable observation supporting a finding or verification run."""

    id: str
    verification_class: VerificationClass
    summary: str
    check_id: Optional[str] = None
    details: Mapping[str, JSONValue] = field(default_factory=dict)
    source: Optional[str] = None
    command: Optional[CommandExecution] = None
    observed_at: Optional[str] = None


@dataclass(frozen=True)
class Finding(JsonModel):
    """A check outcome suitable for presentation and stable deduplication."""

    check_id: str
    title: str
    message: str
    assessment: Assessment
    severity: Severity
    category: CheckCategory
    state: ResultState = ResultState.NOT_VERIFIED
    code: Optional[str] = None
    path: Optional[str] = None
    line: Optional[int] = None
    evidence: Tuple[Evidence, ...] = ()
    remediation: Optional[str] = None
    verification_hint: Optional[str] = None
    false_positive_policy: Optional[str] = None
    ecosystems: Tuple[Ecosystem, ...] = ()
    applicability_reason: Optional[str] = None
    scoring_group: Optional[str] = None

    @property
    def fingerprint(self) -> str:
        """Return the stable SHA-256 fingerprint for this finding."""

        return finding_fingerprint(self)


@dataclass(frozen=True)
class CheckSpec(JsonModel):
    """Declarative metadata for a check implementation."""

    check_id: str
    name: str
    category: CheckCategory
    verification_class: VerificationClass
    description: Optional[str] = None
    ecosystem: Optional[Ecosystem] = None
    commands: Tuple[CommandSpec, ...] = ()
    enabled: bool = True
    severity: Severity = Severity.INFO
    supported_ecosystems: Tuple[Ecosystem, ...] = ()
    limitations: Tuple[str, ...] = ()
    false_positive_policy: Optional[str] = None
    scoring_group: Optional[str] = None


@dataclass(frozen=True)
class RepoFile(JsonModel):
    """A file discovered in the repository."""

    path: str
    size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    language: Optional[str] = None
    is_symlink: bool = False


@dataclass(frozen=True)
class DiscoverySummary(JsonModel):
    """Summary of repository discovery before checks run."""

    files: Tuple[RepoFile, ...] = ()
    ecosystems: Tuple[Ecosystem, ...] = ()
    root: Optional[str] = None
    total_files: int = 0
    ignored_files: int = 0


@dataclass(frozen=True)
class VerificationRun(JsonModel):
    """One static or executed verification attempt."""

    run_id: str
    verification_class: VerificationClass
    state: ResultState = ResultState.NOT_VERIFIED
    commands: Tuple[CommandExecution, ...] = ()
    environment: Optional[EnvironmentFingerprint] = None
    evidence_ids: Tuple[str, ...] = ()
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class RepositoryIdentity(JsonModel):
    """Identity of the repository being checked."""

    root: str
    repository_id: Optional[str] = None
    revision: Optional[str] = None
    branch: Optional[str] = None
    remote_url: Optional[str] = None
    is_dirty: Optional[bool] = None


@dataclass(frozen=True)
class ContentFingerprint(JsonModel):
    """Digest describing repository content or a content manifest."""

    algorithm: str
    digest: str
    file_count: Optional[int] = None


@dataclass(frozen=True)
class IntegrityMetadata(JsonModel):
    """Integrity information attached to an evidence receipt."""

    verified: bool
    content_fingerprint: Optional[ContentFingerprint] = None
    method: Optional[str] = None
    details: Mapping[str, JSONValue] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceReceipt(JsonModel):
    """Portable record of evidence collected for one repository revision."""

    receipt_id: str
    repository: RepositoryIdentity
    content: ContentFingerprint
    integrity: IntegrityMetadata
    evidence: Tuple[Evidence, ...] = ()
    verification_runs: Tuple[VerificationRun, ...] = ()
    schema_version: str = "receipt-v1"
    generated_at: Optional[str] = None


@dataclass(frozen=True)
class Report(JsonModel):
    """Top-level ReproCheck result."""

    repository: RepositoryIdentity
    findings: Tuple[Finding, ...] = ()
    state: ResultState = ResultState.NOT_VERIFIED
    assessment: Assessment = Assessment.UNKNOWN
    checks: Tuple[CheckSpec, ...] = ()
    discovery: Optional[DiscoverySummary] = None
    verification_runs: Tuple[VerificationRun, ...] = ()
    evidence_receipt: Optional[EvidenceReceipt] = None
    generated_at: Optional[str] = None
    summary: Mapping[str, JSONValue] = field(default_factory=dict)
    schema_version: str = "report-v1"


def to_json_dict(value: Any) -> JSONValue:
    """Convert supported model values to JSON-compatible built-in values.

    Dataclass fields, enums, mappings, tuples, lists, and sets are handled
    recursively.  Mapping keys are stringified and sorted so the returned
    structure is deterministic before it reaches ``json.dumps``.
    """

    if isinstance(value, Enum):
        return to_json_dict(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: to_json_dict(getattr(value, item.name)) for item in fields(value)}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): to_json_dict(value[key]) for key in sorted(value, key=lambda item: str(item))
        }
    if isinstance(value, (tuple, list)):
        return [to_json_dict(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [to_json_dict(item) for item in sorted(value, key=repr)]
    raise TypeError(f"unsupported value for JSON serialization: {type(value)!r}")


def dumps_json(value: Any, *, indent: Optional[int] = None) -> str:
    """Serialize a model or JSON-compatible value deterministically."""

    separators = (",", ":") if indent is None else None
    return json.dumps(
        to_json_dict(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        indent=indent,
        separators=separators,
    )


def model_to_dict(model: JsonModel) -> Dict[str, JSONValue]:
    """Explicit function form of :meth:`JsonModel.to_dict`."""

    return model.to_dict()


def model_to_json(model: JsonModel, *, indent: Optional[int] = None) -> str:
    """Explicit function form of :meth:`JsonModel.to_json`."""

    return model.to_json(indent=indent)


def to_json(value: Any, *, indent: Optional[int] = None) -> str:
    """Short alias for :func:`dumps_json`."""

    return dumps_json(value, indent=indent)


_TEMPORAL_KEYS: Set[str] = {
    "created_at",
    "finished_at",
    "generated_at",
    "observed_at",
    "started_at",
    "timestamp",
    "updated_at",
}


def _without_timestamps(value: JSONValue) -> JSONValue:
    """Remove temporal fields from an already JSON-compatible value."""

    if isinstance(value, dict):
        return {
            key: _without_timestamps(item)
            for key, item in value.items()
            if key.lower() not in _TEMPORAL_KEYS
        }
    if isinstance(value, list):
        return [_without_timestamps(item) for item in value]
    return value


def finding_fingerprint(finding: Finding) -> str:
    """Generate a stable SHA-256 fingerprint for a finding.

    The fingerprint uses identifying and explanatory finding data.  Evidence
    timestamps and command execution details are deliberately excluded, so
    collecting the same finding at different times does not create a new
    identity.  The returned value is lowercase hexadecimal.
    """

    evidence = [
        {
            "verification_class": item.verification_class,
            "summary": item.summary,
            "check_id": item.check_id,
            "source": item.source,
            "details": _without_timestamps(to_json_dict(item.details)),
        }
        for item in finding.evidence
    ]
    stable_payload = {
        "check_id": finding.check_id,
        "title": finding.title,
        "message": finding.message,
        "assessment": finding.assessment,
        "severity": finding.severity,
        "category": finding.category,
        "code": finding.code,
        "path": finding.path,
        "line": finding.line,
        "evidence": evidence,
        "remediation": finding.remediation,
    }
    canonical = dumps_json(_without_timestamps(to_json_dict(stable_payload)))
    return sha256(canonical.encode("utf-8")).hexdigest()
