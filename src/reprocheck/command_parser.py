"""Conservative command extraction and planning for static repository scans.

This module turns a small, deliberately allow-listed set of README, package
metadata, and CI command forms into :class:`~reprocheck.models.CommandSpec`
records.  It never invokes a shell, resolves an executable, imports project
code, or runs a command.  Text that cannot be represented safely is retained
as ``unsupported`` evidence rather than being guessed into an executable plan.

The public plan has one classification for each supported intent (currently
``test`` and ``build``): ``recognized``, ``missing``, ``ambiguous``, or
``unsupported``.  A recognized command is a proposal for a future trusted
executor, not proof that the command exists or succeeds.
"""

from __future__ import annotations

import json
import re
import shlex
import tomllib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Final, Iterable, Mapping

from .checks.base import CheckContext, read_known_text
from .command_analysis import readme_command_lines, workflow_jobs
from .discovery import discover_repository, resolve_repository_root
from .models import CommandSpec, DiscoverySummary, JSONValue


class CommandStatus(str, Enum):
    """Classification assigned to one command intent in a repository plan."""

    RECOGNIZED = "recognized"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"


# A compatibility spelling that reads naturally for callers that describe the
# result as a classification rather than a status.
CommandClassification = CommandStatus

COMMAND_INTENTS: Final[tuple[str, ...]] = ("test", "build")

_README_NAMES: Final[frozenset[str]] = frozenset(
    {"readme", "readme.md", "readme.rst", "readme.txt"}
)
_CI_PREFIXES: Final[tuple[str, ...]] = (
    ".github/workflows/",
    ".circleci/",
)
_CI_NAMES: Final[frozenset[str]] = frozenset(
    {".gitlab-ci.yml", ".gitlab-ci.yaml", ".travis.yml", "azure-pipelines.yml"}
)
_SHELL_CONTROL_RE: Final[re.Pattern[str]] = re.compile(r"(?:&&|\|\||[;&|<>`$])")
_LEADING_LABEL_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:run|execute|command|test|build)\s*[:\-]\s*", re.IGNORECASE
)
_KNOWN_EXECUTABLE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:python(?:3(?:\.\d+)?)?|uv|pytest|tox|nox|npm|yarn|pnpm|"
    r"cargo|go|dotnet|mvn|gradle|make|docker)(?:\s|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CommandRecord:
    """One safe command proposal or one bounded classification result.

    ``spec`` is populated only for a recognized command.  ``alternatives``
    contains distinct recognized specs when an intent is ambiguous.  The
    ``source`` value is always repository-relative when it came from a
    repository scan.
    """

    intent: str
    status: CommandStatus
    spec: CommandSpec | None = None
    source: str | None = None
    raw: str = ""
    reason: str | None = None
    sources: tuple[str, ...] = ()
    alternatives: tuple[CommandSpec, ...] = ()

    @property
    def command(self) -> CommandSpec | None:
        """Return the planned command, using a convenient executor-like name."""

        return self.spec

    @property
    def command_spec(self) -> CommandSpec | None:
        """Alias for :attr:`spec` for callers using model terminology."""

        return self.spec

    @property
    def executable(self) -> str | None:
        """Return the proposed executable, if this record is recognized."""

        return self.spec.executable if self.spec is not None else None

    @property
    def args(self) -> tuple[str, ...]:
        """Return proposed arguments, or an empty tuple when none are safe."""

        return self.spec.args if self.spec is not None else ()

    def to_dict(self) -> dict[str, JSONValue]:
        """Return a deterministic JSON-compatible representation."""

        return {
            "intent": self.intent,
            "status": self.status.value,
            "spec": self.spec.to_dict() if self.spec is not None else None,
            "source": self.source,
            "raw": self.raw,
            "reason": self.reason,
            "sources": list(self.sources),
            "alternatives": [alternative.to_dict() for alternative in self.alternatives],
        }


@dataclass(frozen=True)
class CommandPlan:
    """Deterministic command classifications for a repository."""

    records: tuple[CommandRecord, ...] = ()

    @property
    def recognized(self) -> tuple[CommandRecord, ...]:
        return tuple(record for record in self.records if record.status is CommandStatus.RECOGNIZED)

    @property
    def missing(self) -> tuple[CommandRecord, ...]:
        return tuple(record for record in self.records if record.status is CommandStatus.MISSING)

    @property
    def ambiguous(self) -> tuple[CommandRecord, ...]:
        return tuple(record for record in self.records if record.status is CommandStatus.AMBIGUOUS)

    @property
    def unsupported(self) -> tuple[CommandRecord, ...]:
        return tuple(
            record for record in self.records if record.status is CommandStatus.UNSUPPORTED
        )

    @property
    def commands(self) -> tuple[CommandSpec, ...]:
        """Return recognized command specs in intent order."""

        return tuple(record.spec for record in self.recognized if record.spec is not None)

    def to_dict(self) -> dict[str, JSONValue]:
        """Return a deterministic JSON-compatible representation."""

        return {
            "records": [record.to_dict() for record in self.records],
            "recognized": len(self.recognized),
            "missing": len(self.missing),
            "ambiguous": len(self.ambiguous),
            "unsupported": len(self.unsupported),
        }


@dataclass(frozen=True)
class _Candidate:
    intent: str
    spec: CommandSpec | None
    source: str
    raw: str
    reason: str | None = None


def _spec(executable: str, args: Iterable[str], *, description: str) -> CommandSpec:
    return CommandSpec(
        executable=executable,
        args=tuple(args),
        description=description,
    )


def _tokens(text: str) -> tuple[str, ...] | None:
    """Split a command only when it contains no shell control language."""

    candidate = text.strip()
    if candidate.startswith(("$ ", "> ")):
        candidate = candidate[2:].lstrip()
    candidate = _LEADING_LABEL_RE.sub("", candidate)
    if not candidate or _SHELL_CONTROL_RE.search(candidate):
        return None
    try:
        values = tuple(shlex.split(candidate, posix=True))
    except ValueError:
        return None
    if not values or not _KNOWN_EXECUTABLE_RE.match(values[0]):
        return None
    if any("\x00" in value for value in values):
        return None
    return values


def _recognized_spec(tokens: tuple[str, ...], *, source: str) -> tuple[str, CommandSpec] | None:
    executable = tokens[0].lower()
    args = tokens[1:]
    description = f"Conservative {source} command"

    if executable in {"python", "python3"}:
        if (
            len(args) >= 2
            and args[0] == "-m"
            and args[1].lower() in {"pytest", "unittest", "tox", "nox"}
        ):
            return "test", _spec(tokens[0], args, description=description)
        if len(args) >= 2 and args[0] == "-m" and args[1].lower() == "build":
            return "build", _spec(tokens[0], args, description=description)
        return None
    if executable == "uv":
        normalized = list(args)
        while normalized and normalized[0] in {"--locked", "--frozen"}:
            normalized.pop(0)
        if normalized and normalized[0].lower() == "run":
            normalized.pop(0)
            while normalized and normalized[0].startswith("-"):
                normalized.pop(0)
            if normalized and normalized[0].lower() in {"pytest", "tox", "nox"}:
                return "test", _spec(tokens[0], args, description=description)
            if (
                len(normalized) >= 3
                and normalized[0].lower() in {"python", "python3"}
                and normalized[1] == "-m"
                and normalized[2].lower() in {"pytest", "unittest", "tox", "nox"}
            ):
                return "test", _spec(tokens[0], args, description=description)
        if normalized and normalized[0].lower() == "build":
            return "build", _spec(tokens[0], args, description=description)
        return None
    if executable in {"pytest", "tox", "nox"}:
        return "test", _spec(tokens[0], args, description=description)
    if executable in {"npm", "yarn", "pnpm"}:
        script = args[1] if len(args) >= 2 and args[0].lower() == "run" else args[0] if args else ""
        if script.lower() in {"test", "check", "lint", "typecheck"}:
            return "test", _spec(tokens[0], args, description=description)
        if script.lower() == "build":
            return "build", _spec(tokens[0], args, description=description)
        return None
    if executable in {"cargo", "go", "dotnet"} and args and args[0].lower() in {"test", "build"}:
        return args[0].lower(), _spec(tokens[0], args, description=description)
    if executable in {"mvn", "gradle", "make"} and args:
        if args[0].lower() == "test":
            return "test", _spec(tokens[0], args, description=description)
        if args[0].lower() in {"build", "package"}:
            return "build", _spec(tokens[0], args, description=description)
    if executable == "docker" and args and args[0].lower() == "build":
        return "build", _spec(tokens[0], args, description=description)
    return None


def _candidate_from_text(text: str, *, source: str) -> _Candidate | None:
    values = _tokens(text)
    if values is None:
        return _Candidate(
            intent="unknown",
            spec=None,
            source=source,
            raw=text.strip()[:400],
            reason="command is empty, uses shell syntax, or is not an allow-listed form",
        )
    parsed = _recognized_spec(values, source=source)
    if parsed is None:
        return _Candidate(
            intent="unknown",
            spec=None,
            source=source,
            raw=text.strip()[:400],
            reason="executable or arguments are not a recognized test/build form",
        )
    intent, command = parsed
    return _Candidate(intent, command, source, text.strip()[:400])


def _candidate_records(candidates: Iterable[_Candidate]) -> tuple[CommandRecord, ...]:
    """Classify candidates by intent, collapsing identical source proposals."""

    by_intent: dict[str, list[_Candidate]] = {intent: [] for intent in COMMAND_INTENTS}
    for candidate in candidates:
        if candidate.intent in by_intent:
            by_intent[candidate.intent].append(candidate)

    records: list[CommandRecord] = []
    for intent in COMMAND_INTENTS:
        candidates_for_intent = by_intent[intent]
        recognized_candidates = [item for item in candidates_for_intent if item.spec is not None]
        unsupported_candidates = [item for item in candidates_for_intent if item.spec is None]
        distinct: dict[str, CommandSpec] = {}
        for candidate in recognized_candidates:
            if candidate.spec is not None:
                distinct.setdefault(_canonical_spec(candidate.spec), candidate.spec)
        if len(distinct) == 1:
            command = next(iter(distinct.values()))
            sources = tuple(sorted({item.source for item in recognized_candidates}))
            raw = min(item.raw for item in recognized_candidates)
            records.append(
                CommandRecord(
                    intent=intent,
                    status=CommandStatus.RECOGNIZED,
                    spec=command,
                    source=sources[0] if sources else None,
                    raw=raw,
                    sources=sources,
                )
            )
        elif len(distinct) > 1:
            alternatives = tuple(distinct[key] for key in sorted(distinct))
            sources = tuple(sorted({item.source for item in recognized_candidates}))
            records.append(
                CommandRecord(
                    intent=intent,
                    status=CommandStatus.AMBIGUOUS,
                    source=sources[0] if sources else None,
                    raw=min(item.raw for item in recognized_candidates),
                    reason="multiple distinct recognized command plans were found",
                    sources=sources,
                    alternatives=alternatives,
                )
            )
        else:
            if unsupported_candidates:
                item = sorted(unsupported_candidates, key=lambda value: (value.source, value.raw))[
                    0
                ]
                sources = tuple(sorted({value.source for value in unsupported_candidates}))
                records.append(
                    CommandRecord(
                        intent=intent,
                        status=CommandStatus.UNSUPPORTED,
                        source=item.source,
                        raw=item.raw,
                        reason=item.reason,
                        sources=sources,
                    )
                )
            else:
                records.append(
                    CommandRecord(
                        intent=intent,
                        status=CommandStatus.MISSING,
                        reason=f"no recognized {intent} command was found",
                    )
                )
    return tuple(records)


def _canonical_spec(spec: CommandSpec) -> str:
    return "\x1f".join((spec.executable, *spec.args))


def _intent_hint(raw: str) -> str | None:
    lowered = raw.lower()
    if re.search(r"\b(tests?|pytest|tox|nox)\b", lowered):
        return "test"
    if re.search(r"\b(build|package)\b", lowered):
        return "build"
    return None


def parse_command_text(text: str, *, source: str = "text") -> tuple[CommandRecord, ...]:
    """Parse one command-bearing text value without shell interpretation.

    This function is intended for a single README code line, package script,
    or CI ``run`` value.  It returns zero or one candidate record; unknown
    intent is returned as an ``unsupported`` record so callers do not confuse
    an explicit but unsupported command with a missing command.
    """

    candidate = _candidate_from_text(text, source=source)
    if candidate is None or candidate.intent == "unknown":
        hint = _intent_hint(text)
        return (
            CommandRecord(
                intent=hint or "unknown",
                status=CommandStatus.UNSUPPORTED,
                source=source,
                raw=text.strip()[:400],
                reason=candidate.reason if candidate is not None else "command could not be parsed",
                sources=(source,),
            ),
        )
    return (
        CommandRecord(
            intent=candidate.intent,
            status=CommandStatus.RECOGNIZED,
            spec=candidate.spec,
            source=source,
            raw=candidate.raw,
            sources=(source,),
        ),
    )


def extract_commands(text: str, *, source: str = "text") -> tuple[CommandRecord, ...]:
    """Extract recognized command records from simple command-bearing text."""

    records: list[CommandRecord] = []
    for line in text.splitlines() or [text]:
        candidate = _candidate_from_text(line, source=source)
        if candidate is None or candidate.intent == "unknown":
            continue
        records.append(
            CommandRecord(
                intent=candidate.intent,
                status=CommandStatus.RECOGNIZED,
                spec=candidate.spec,
                source=source,
                raw=candidate.raw,
                sources=(source,),
            )
        )
    return tuple(records)


def _readme_candidates(text: str, *, source: str) -> tuple[_Candidate, ...]:
    candidates: list[_Candidate] = []
    for command in readme_command_lines(text):
        snippet = command.raw
        candidate = _candidate_from_text(snippet, source=source)
        if candidate is not None and candidate.intent != "unknown":
            candidates.append(candidate)
        elif _intent_hint(snippet) is not None and (
            _KNOWN_EXECUTABLE_RE.match(snippet)
            or snippet.startswith(("./", "$ ", "> "))
            or re.match(r"^[\w./-]+\.(?:sh|ps1|bat)(?:\s|$)", snippet)
        ):
            candidates.append(
                _Candidate(
                    intent=_intent_hint(snippet) or "unknown",
                    spec=None,
                    source=source,
                    raw=snippet.strip()[:400],
                    reason="README command uses unsupported or unsafe shell syntax",
                )
            )
    return tuple(candidates)


def _script_candidates(path: str, text: str) -> tuple[_Candidate, ...]:
    name = PurePosixPath(path).name.lower()
    try:
        parsed: object = json.loads(text) if name == "package.json" else tomllib.loads(text)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
        return tuple(
            _Candidate(intent, None, path, "", f"{path} could not be parsed: {error}")
            for intent in COMMAND_INTENTS
        )
    if not isinstance(parsed, dict):
        return tuple(
            _Candidate(intent, None, path, "", f"{path} is not an object")
            for intent in COMMAND_INTENTS
        )

    scripts: Mapping[object, object] = {}
    if name == "package.json":
        value = parsed.get("scripts", {})
        if isinstance(value, dict):
            scripts = value
        elif "scripts" in parsed:
            return tuple(
                _Candidate(
                    intent,
                    None,
                    path,
                    "",
                    "package.json scripts is not an object",
                )
                for intent in COMMAND_INTENTS
            )
    else:
        project = parsed.get("project")
        tool = parsed.get("tool")
        tables: list[object] = []
        if isinstance(project, dict):
            tables.append(project.get("scripts", {}))
        poetry = tool.get("poetry") if isinstance(tool, dict) else None
        if isinstance(poetry, dict):
            tables.append(poetry.get("scripts", {}))
        for table in tables:
            if isinstance(table, dict):
                scripts = {**scripts, **table}
            elif table != {}:
                return tuple(
                    _Candidate(
                        intent,
                        None,
                        path,
                        "",
                        "Python project scripts is not a table",
                    )
                    for intent in COMMAND_INTENTS
                )

    candidates: list[_Candidate] = []
    for script_name, script_value in sorted(scripts.items(), key=lambda item: str(item[0])):
        if not isinstance(script_name, str) or not isinstance(script_value, str):
            continue
        intent = _script_intent(script_name)
        if intent is None:
            continue
        candidate = _candidate_from_text(script_value, source=path)
        if candidate is not None and candidate.intent == intent and candidate.spec is not None:
            candidates.append(candidate)
            continue
        if name == "package.json":
            raw = script_value
        else:
            raw = f"{script_name} = {script_value}"
        candidates.append(
            _Candidate(
                intent=intent,
                spec=None,
                source=path,
                raw=raw[:400],
                reason="script is declared but its command form is unsupported or ambiguous",
            )
        )
    return tuple(candidates)


def _script_intent(name: str) -> str | None:
    normalized = name.lower().split(":", 1)[0]
    if normalized in {"test", "check", "lint", "typecheck"}:
        return "test"
    if normalized == "build":
        return "build"
    return None


def _ci_candidates(text: str, *, source: str) -> tuple[_Candidate, ...]:
    candidates: list[_Candidate] = []
    if source.lower().startswith(".github/workflows/"):
        for job in workflow_jobs(text, source):
            for command in job.commands:
                for line in command.raw.splitlines():
                    if not line.strip() or line.lstrip().startswith("#"):
                        continue
                    candidate = _candidate_from_text(line, source=source)
                    if candidate is not None and candidate.intent != "unknown":
                        candidates.append(candidate)
                    elif _intent_hint(line) is not None:
                        candidates.append(
                            _Candidate(
                                _intent_hint(line) or "unknown",
                                None,
                                source,
                                line[:400],
                                "CI command uses unsupported or unsafe shell syntax",
                            )
                        )
        return tuple(candidates)
    block_indent: int | None = None
    for raw_line in text.splitlines():
        indentation = len(raw_line) - len(raw_line.lstrip())
        match = re.match(r"^\s*(?:-\s*)?(?:run|script|before_script)\s*:\s*(.*?)\s*$", raw_line)
        if match is None and block_indent is not None:
            if not raw_line.strip() or indentation <= block_indent:
                block_indent = None
                continue
            value = re.sub(r"^\s*-\s*", "", raw_line).strip()
            candidate = _candidate_from_text(value, source=source)
            if candidate is not None and candidate.intent != "unknown":
                candidates.append(candidate)
            continue
        if match is None:
            continue
        value = match.group(1).strip()
        if value in {"|", ">"}:
            block_indent = indentation
            continue
        block_indent = None
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        candidate = _candidate_from_text(value, source=source)
        if candidate is not None and candidate.intent != "unknown":
            candidates.append(candidate)
        else:
            hint = _intent_hint(value)
            if hint is not None:
                candidates.append(
                    _Candidate(
                        hint,
                        None,
                        source,
                        value[:400],
                        "CI command uses unsupported or unsafe shell syntax",
                    )
                )
    return tuple(candidates)


def _is_ci_path(path: str) -> bool:
    lowered = path.lower()
    return (
        lowered.startswith(_CI_PREFIXES) and lowered.endswith((".yml", ".yaml"))
    ) or lowered in _CI_NAMES


def plan_repository_commands(
    repository: Path,
    *,
    discovery: DiscoverySummary | None = None,
) -> CommandPlan:
    """Build a safe command plan from discovered README/package/CI sources."""

    root = resolve_repository_root(Path(repository))
    summary = discovery if discovery is not None else discover_repository(root)
    context = CheckContext(repository_root=root, discovery=summary)
    candidates: list[_Candidate] = []
    for repo_file in summary.files:
        if repo_file.is_symlink:
            continue
        path = repo_file.path
        basename = PurePosixPath(path).name.lower()
        if basename in _README_NAMES and len(PurePosixPath(path).parts) == 1:
            result = read_known_text(context, path)
            if result.status == "ok":
                candidates.extend(_readme_candidates(result.text, source=path))
            else:
                candidates.extend(
                    _Candidate(intent, None, path, "", result.error or result.status)
                    for intent in COMMAND_INTENTS
                )
        elif len(PurePosixPath(path).parts) == 1 and basename in {"package.json", "pyproject.toml"}:
            result = read_known_text(context, path)
            if result.status == "ok":
                candidates.extend(_script_candidates(path, result.text))
            else:
                candidates.extend(
                    _Candidate(intent, None, path, "", result.error or result.status)
                    for intent in COMMAND_INTENTS
                )
        elif _is_ci_path(path):
            result = read_known_text(context, path)
            if result.status == "ok":
                candidates.extend(_ci_candidates(result.text, source=path))
            else:
                candidates.extend(
                    _Candidate(intent, None, path, "", result.error or result.status)
                    for intent in COMMAND_INTENTS
                )
    return CommandPlan(_candidate_records(candidates))


def extract_repository_commands(
    repository: Path,
    *,
    discovery: DiscoverySummary | None = None,
) -> CommandPlan:
    """Named alias for :func:`plan_repository_commands`."""

    return plan_repository_commands(repository, discovery=discovery)


def plan_commands(repository: Path, *, discovery: DiscoverySummary | None = None) -> CommandPlan:
    """Short alias for :func:`plan_repository_commands`."""

    return plan_repository_commands(repository, discovery=discovery)


def parse_command(text: str, *, source: str = "text") -> tuple[CommandRecord, ...]:
    """Short alias for :func:`parse_command_text`."""

    return parse_command_text(text, source=source)


def parse_repository_commands(
    repository: Path,
    *,
    discovery: DiscoverySummary | None = None,
) -> CommandPlan:
    """Short alias for :func:`plan_repository_commands`."""

    return plan_repository_commands(repository, discovery=discovery)


__all__ = [
    "COMMAND_INTENTS",
    "CommandClassification",
    "CommandPlan",
    "CommandRecord",
    "CommandStatus",
    "extract_commands",
    "extract_repository_commands",
    "parse_command",
    "parse_command_text",
    "parse_repository_commands",
    "plan_commands",
    "plan_repository_commands",
]
