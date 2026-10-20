"""Bounded static command-source analysis shared by the command and CI checks.

Only literal argv, root Makefile rules/defaults, and indentation-delimited GitHub
run steps are understood. This is evidence analysis, never a shell/Make/YAML engine.
Make defaults are evaluated without consulting the process environment. External
overrides may change the real command; findings describe only declared defaults.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .checks.base import CheckContext

MAKE_DEFAULT_POLICY = (
    "Make analysis assumes declared root defaults; environment, command-line and target "
    "overrides may change actual commands and are not evaluated."
)


@dataclass(frozen=True)
class CommandLine:
    raw: str
    line: int


@dataclass(frozen=True)
class WorkflowJob:
    name: str
    commands: tuple[CommandLine, ...]
    setup: tuple[int, ...] = ()
    reason: str | None = None
    runner: bool = False


@dataclass(frozen=True)
class CommandLeaf:
    intent: str
    family: str
    identity: tuple[str, ...]
    raw: str
    source: str = ""
    line: int | None = None
    target: str | None = None
    make_defaults: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class CommandAnalysis:
    leaves: tuple[CommandLeaf, ...] = ()
    reason: str | None = None


def literal_tokens(raw: str) -> tuple[str, ...] | None:
    """Reject shell language even inside quotes; strip only shell comments."""
    if re.search(r"[;&|<>`$*?\[\]\n\r\x00]", raw):
        return None
    try:
        return tuple(shlex.split(raw, comments=True))
    except ValueError:
        return None


def _unwrapped(tokens: tuple[str, ...]) -> tuple[str, ...]:
    if tokens and tokens[0] == "uv":
        index = 1
        while index < len(tokens) and tokens[index] in {"--locked", "--offline", "--frozen"}:
            index += 1
        if index >= len(tokens) or tokens[index] != "run":
            return tokens
        index += 1
        while index < len(tokens) and tokens[index] in {"--locked", "--offline", "--frozen"}:
            index += 1
        tokens = tokens[index:]
    if len(tokens) > 2 and re.fullmatch(r"python(?:3(?:\.\d+)?)?", tokens[0]):
        if tokens[1] == "-m":
            tokens = tokens[2:]
    return tokens


def _leaf(raw: str, tokens: tuple[str, ...]) -> CommandLeaf | None:
    # Import lazily: command_parser itself imports checks.base through checks.
    from .command_parser import CommandStatus, parse_command_text

    concrete = _unwrapped(tokens)
    if not concrete or concrete[0].startswith("-"):
        return None
    record = parse_command_text(shlex.join(tokens))[0]
    if record.status is not CommandStatus.RECOGNIZED or record.spec is None:
        return None
    executable = concrete[0]
    if executable == "make":
        return None  # Target names alone are never concrete test evidence.
    if executable in {"npm", "yarn", "pnpm"}:
        arguments = concrete[1:]
        script = (
            arguments[1]
            if len(arguments) > 1 and arguments[0] == "run"
            else (arguments[0] if arguments else "")
        )
        if script not in {"test", "build"}:
            return None  # Lint/typecheck aliases do not establish actual test coverage.
    family = {
        "pytest": "python",
        "unittest": "python",
        "tox": "python",
        "nox": "python",
        "build": "python",
        "uv": "python",
        "npm": "node",
        "yarn": "node",
        "pnpm": "node",
        "cargo": "rust",
        "go": "go",
        "dotnet": "dotnet",
        "mvn": "java",
        "gradle": "java",
        "docker": "container",
    }.get(executable, "script")
    # Presentation flags do not select a different suite. Keep runner, target,
    # selectors, and all other arguments so genuine differences remain visible.
    identity = tuple(value for value in concrete if value not in {"-q", "--quiet", "-v", "-vv"})
    if executable in {"npm", "yarn", "pnpm"} and len(identity) > 2 and identity[1] == "run":
        identity = (identity[0], *identity[2:])
    return CommandLeaf(record.intent, family, identity, raw)


def is_setup_command(raw: str) -> bool:
    tokens = literal_tokens(raw)
    if not tokens:
        return False
    command = _unwrapped(tokens)
    if len(command) < 2:
        return False
    executable, verb = command[:2]
    return (
        executable in {"pip", "pip3", "uv", "poetry", "npm", "pnpm", "yarn", "cargo"}
        and verb in {"install", "sync", "ci", "fetch"}
    ) or command[:3] in {("go", "mod", "download"), ("uv", "python", "install")}


def analyze_command(
    raw: str,
    context: CheckContext | None = None,
    *,
    stack: tuple[str, ...] = (),
    budget: list[int] | None = None,
) -> CommandAnalysis:
    if budget is None:
        budget = [128]
    leaves: list[CommandLeaf] = []
    reasons: list[str] = []
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        tokens = literal_tokens(line)
        if tokens is None:
            reasons.append("shell syntax or variable expansion is unsupported")
        elif not tokens:
            continue
        elif tokens[0] == "make":
            if context is None:
                reasons.append("Makefile resolution requires a repository context")
            else:
                analysis = _resolve_make(tokens[1:], context, stack, budget)
                leaves.extend(analysis.leaves)
                if analysis.reason:
                    reasons.append(analysis.reason)
        else:
            leaf = _leaf(line, tokens)
            if leaf is not None:
                leaves.append(leaf)
            elif not is_setup_command(line) and (
                not _unwrapped(tokens)
                or _unwrapped(tokens)[0] not in {"echo", "true", "ruff", "mypy"}
            ):
                reasons.append("command is outside the bounded test/build forms")
    return CommandAnalysis(tuple(leaves), "; ".join(dict.fromkeys(reasons)) or None)


@dataclass
class _MakeRule:
    prerequisites: tuple[str, ...]
    recipes: list[CommandLine]
    make_defaults: tuple[tuple[str, str], ...] = ()


_MAKE_REFERENCE = re.compile(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)|\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_MAKE_CONTROL_VARIABLES = frozenset(
    {
        "MAKE",
        "MAKEFLAGS",
        "MFLAGS",
        "MAKEFILES",
        "MAKEOVERRIDES",
        "MAKELEVEL",
        "GNUMAKEFLAGS",
        "SHELL",
        "PATH",
        "VPATH",
        "ENV",
        "BASH_ENV",
        "PYTHONPATH",
        "PYTHONHOME",
    }
)


def _expand_make_defaults(
    text: str, variables: dict[str, str]
) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Expand named declared defaults only, with no built-ins, functions or env.

    The caller freezes := and prerequisite expansions at their declaration point;
    recursive =/?= values use the declarations in effect when a recipe is read.
    Unknown variables are uncertainty, never GNU Make's implicit empty string.
    """
    remaining = [128]
    used: dict[str, str] = {}

    def expand(value: str, ancestors: tuple[str, ...]) -> str:
        if len(value) > 16384:
            raise ValueError("Make variable expansion exceeds the text bound")

        def replace(match: re.Match[str]) -> str:
            name = match[1] or match[2]
            remaining[0] -= 1
            if name in ancestors or len(ancestors) >= 16 or remaining[0] < 0:
                raise ValueError("Make variable cycle or expansion bound reached")
            if name not in variables:
                raise ValueError(f"Make variable {name} has no declared default")
            expanded = expand(variables[name], (*ancestors, name))
            used[name] = expanded
            return expanded

        expanded = _MAKE_REFERENCE.sub(replace, value)
        if len(expanded) > 16384:
            raise ValueError("Make variable expansion exceeds the text bound")
        if literal_tokens(expanded) is None or "\\" in expanded:
            raise ValueError("dynamic Make variables, functions or shell syntax are unsupported")
        return expanded

    return expand(text, ()), tuple(sorted(used.items()))


def _resolve_make(
    targets: tuple[str, ...], context: CheckContext, stack: tuple[str, ...], budget: list[int]
) -> CommandAnalysis:
    from .checks.base import read_known_text, top_level_paths

    if not targets or any(not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", t) for t in targets):
        return CommandAnalysis(
            reason="only explicit literal Make targets without options are supported"
        )
    paths = set(top_level_paths(context))
    makefiles = [path for path in ("GNUmakefile", "makefile", "Makefile") if path in paths]
    if not makefiles:
        return CommandAnalysis(reason="no discovered root Makefile can resolve the target")
    path = makefiles[0]  # GNU Make's documented default-file precedence.
    result = read_known_text(context, path)
    if result.status != "ok":
        return CommandAnalysis(reason=f"{path}: {result.error or result.status}")
    rules: dict[str, _MakeRule] = {}
    variables: dict[str, str] = {}
    assignments = 0
    current: _MakeRule | None = None
    for number, raw in enumerate(result.text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("\t"):
            if current is None:
                return CommandAnalysis(reason=f"{path}:{number}: orphan Make recipe")
            current.recipes.append(CommandLine(raw[1:].lstrip("@"), number))
            continue
        current = None
        stripped = raw.split("#", 1)[0].strip()
        assignment = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*(=|:=|\?=)\s*(.*)", stripped)
        if assignment:
            name, operator, value = assignment.groups()
            assignments += 1
            if (
                name in _MAKE_CONTROL_VARIABLES
                or assignments > 128
                or (name not in variables and len(variables) >= 64)
            ):
                return CommandAnalysis(reason=f"{path}:{number}: Make control variable or bound")
            # Reject dynamic syntax even in defaults that are not selected by ?=.
            template = _MAKE_REFERENCE.sub("make_literal", value)
            if literal_tokens(template) is None or "\\" in template:
                return CommandAnalysis(reason=f"{path}:{number}: unsupported Make default syntax")
            if operator == ":=":
                try:
                    value, _ = _expand_make_defaults(value, variables)
                except ValueError as error:
                    return CommandAnalysis(reason=f"{path}:{number}: {error}")
            if operator != "?=" or name not in variables:
                variables[name] = value
            continue
        if stripped.startswith(".PHONY:"):
            try:
                _expand_make_defaults(stripped.partition(":")[2], variables)
            except ValueError as error:
                return CommandAnalysis(reason=f"{path}:{number}: {error}")
            continue
        match = re.fullmatch(r"([A-Za-z0-9_][A-Za-z0-9_.-]*):\s*(.*?)", stripped)
        if match is None:
            return CommandAnalysis(reason=f"{path}:{number}: unsupported Make directive or rule")
        name, prerequisites = match.groups()
        try:
            prerequisites, defaults = _expand_make_defaults(prerequisites, variables)
        except ValueError as error:
            return CommandAnalysis(reason=f"{path}:{number}: {error}")
        if any(c in prerequisites for c in "%:;\\|=*?[]"):
            return CommandAnalysis(reason=f"{path}:{number}: unsupported Make prerequisites")
        if name in rules:
            return CommandAnalysis(reason=f"{path}:{number}: duplicate Make target {name}")
        current = _MakeRule(tuple(prerequisites.split()), [], defaults)
        rules[name] = current
    # Unused dynamic/unknown defaults also keep this bounded subset uncertain.
    for name in variables:
        try:
            _expand_make_defaults(f"$({name})", variables)
        except ValueError as error:
            return CommandAnalysis(reason=f"{path}: {error}")
    leaves: list[CommandLeaf] = []
    reasons: list[str] = []

    def visit(
        target: str,
        ancestors: tuple[str, ...],
        inherited_defaults: tuple[tuple[str, str], ...] = (),
    ) -> None:
        key = f"{path}:{target}"
        budget[0] -= 1
        if key in ancestors or len(ancestors) >= 16 or budget[0] < 0:
            reasons.append(f"{key}: Make cycle or traversal bound reached")
            return
        rule = rules.get(target)
        if rule is None:
            reasons.append(f"{key}: prerequisite/target has no literal rule")
            return
        defaults = tuple(sorted({*inherited_defaults, *rule.make_defaults}))
        for prerequisite in rule.prerequisites:
            if budget[0] <= 0:
                reasons.append("Make traversal bound reached")
                break
            visit(prerequisite, (*ancestors, key), defaults)
        for recipe in rule.recipes:
            if budget[0] <= 0:
                reasons.append("Make traversal bound reached")
                break
            budget[0] -= 1
            try:
                expanded, recipe_defaults = _expand_make_defaults(recipe.raw, variables)
            except ValueError as error:
                reasons.append(f"{key}:{recipe.line}: {error}")
                continue
            analysis = analyze_command(expanded, context, stack=(*ancestors, key), budget=budget)
            for leaf in analysis.leaves:
                leaves.append(
                    CommandLeaf(
                        leaf.intent,
                        leaf.family,
                        leaf.identity,
                        leaf.raw,
                        leaf.source or path,
                        leaf.line or recipe.line,
                        leaf.target or target,
                        tuple(sorted({*defaults, *recipe_defaults, *leaf.make_defaults})),
                    )
                )
            if analysis.reason:
                reasons.append(f"{key}:{recipe.line}: {analysis.reason}")

    for target in targets:
        if budget[0] <= 0:
            reasons.append("Make traversal bound reached")
            break
        visit(target, stack)
    return CommandAnalysis(tuple(leaves), "; ".join(dict.fromkeys(reasons)) or None)


_ALTERNATIVE = re.compile(r"(?i)(?:\b(?:fallback|alternative)\b|\bwithout (?:GNU )?Make\b)")


def readme_command_lines(text: str) -> tuple[CommandLine, ...]:
    """Extract invocation snippets, excluding tables, code prose and labeled alternatives."""
    result: list[CommandLine] = []
    fence: str | None = None
    shell_fence = False
    alternative_section: int | None = None
    alternative_paragraph = False
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        marker = re.match(r"^(`{3,}|~{3,})(\w*)", line)
        if marker:
            if fence is None:
                fence = marker[1][0]
                shell_fence = marker[2].lower() in {
                    "",
                    "sh",
                    "shell",
                    "bash",
                    "console",
                    "powershell",
                    "ps1",
                    "zsh",
                }
            elif marker[1][0] == fence:
                fence = None
                alternative_paragraph = False
            continue
        if fence is not None:
            if shell_fence and not alternative_paragraph and alternative_section is None:
                result.append(CommandLine(line.removeprefix("$ "), number))
            continue
        heading = re.match(r"^(#{1,6})\s+", line)
        if heading:
            if alternative_section is not None and len(heading[1]) <= alternative_section:
                alternative_section = None
            if _ALTERNATIVE.search(line):
                alternative_section = len(heading[1])
            alternative_paragraph = False
            continue
        if not line or line.startswith(("|", ">", "<!--")):
            continue
        if _ALTERNATIVE.search(line):
            alternative_paragraph = True
            continue
        # An explicit invocation paragraph resets a preceding optional snippet label.
        cue = re.search(
            r"(?i)\b(?:run|execute|build|test|with|using|via)\b", re.sub(r"`[^`]*`", "", line)
        )
        if cue and alternative_paragraph:
            alternative_paragraph = False
        if alternative_section is not None or alternative_paragraph:
            continue
        snippets = re.findall(r"`([^`\n]+)`", raw)
        if snippets:
            if cue or re.fullmatch(r"`[^`]+`[.!]?", line):
                result.extend(CommandLine(snippet, number) for snippet in snippets)
        else:
            result.append(CommandLine(re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", line), number))
    return tuple(result)


def _yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value.split(" #", 1)[0].rstrip()


def workflow_jobs(text: str, path: str) -> tuple[WorkflowJob, ...]:
    """Read only direct GitHub job steps, never nested action inputs or names.

    Other providers and flow-style YAML deliberately remain unsupported rather
    than flattening scripts from independent scopes into a passing workflow.
    """
    if not path.lower().startswith(".github/workflows/"):
        return (WorkflowJob("unknown", (), reason="CI provider scope is not statically supported"),)
    lines = text.splitlines()
    jobs: list[WorkflowJob] = []
    jobs_indent: int | None = None
    job_indent: int | None = None
    job_name = ""
    steps_indent: int | None = None
    step_indent: int | None = None
    commands: list[CommandLine] = []
    setup: list[int] = []
    runner = False
    global_reason = (
        "workflow run defaults are unsupported" if re.search(r"(?m)^defaults\s*:", text) else None
    )
    reason: str | None = global_reason

    def finish() -> None:
        if job_name:
            jobs.append(WorkflowJob(job_name, tuple(commands), tuple(setup), reason, runner))

    index = 0
    while index < len(lines):
        raw = lines[index]
        number = index + 1
        index += 1
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        if re.fullmatch(r"jobs:\s*(?:#.*)?", line) and indent == 0:
            jobs_indent = indent
            continue
        if jobs_indent is None:
            continue
        if indent <= jobs_indent:
            finish()
            job_name = ""
            jobs_indent = None
            continue
        job_match = re.fullmatch(r"([\w-]+):\s*(?:#.*)?", line)
        if job_match and (job_indent is None or indent == job_indent):
            finish()
            job_indent = indent
            job_name = job_match[1]
            steps_indent = step_indent = None
            commands = []
            setup = []
            runner = False
            reason = global_reason
            continue
        if job_indent is None:
            continue
        if indent == job_indent + 2 and line.startswith("runs-on:"):
            runner = True
        if indent == job_indent + 2 and line == "steps:":
            steps_indent = indent
            continue
        if indent == job_indent + 2 and line.startswith(("defaults:", "uses:")):
            reason = "job defaults or reusable workflows are unsupported"
        if steps_indent is None:
            continue
        if indent <= steps_indent:
            steps_indent = step_indent = None
            continue
        if line.startswith("- ") and (step_indent is None or indent == step_indent):
            step_indent = indent
            content = line[2:]
        elif step_indent is not None and indent == step_indent + 2:
            content = line
        else:
            continue
        if content.startswith(("working-directory:", "shell:")):
            reason = "custom step working-directory or shell is unsupported"
        if content.startswith("uses:"):
            action = _yaml_scalar(content[5:])
            if re.fullmatch(r"(?:actions/setup-[\w-]+|astral-sh/setup-uv)@[^\s]+", action):
                setup.append(number)
        if not content.startswith("run:"):
            continue
        value = content[4:].strip()
        if re.fullmatch(r"[|>][-+]?", value):
            block: list[str] = []
            block_number = index + 1
            # Property key column, not the '-' column.
            key_indent = step_indent + 2 if step_indent is not None else indent
            while index < len(lines):
                next_line = lines[index]
                next_indent = len(next_line) - len(next_line.lstrip())
                if next_line.strip() and next_indent <= key_indent:
                    break
                block.append(next_line)
                index += 1
            if value.startswith(">"):
                value = " ".join(part.strip() for part in block)
            else:
                value = "\n".join(part.strip() for part in block)
            commands.append(CommandLine(value, block_number))
        elif value and not value.startswith(("[", "{", "&", "*", "!")):
            commands.append(CommandLine(_yaml_scalar(value), number))
        else:
            reason = "run value uses unsupported YAML syntax"
    finish()
    return tuple(jobs)
