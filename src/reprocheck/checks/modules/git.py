"""Static .gitignore and generated-artifact hygiene check."""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from ...models import (
    Assessment,
    CheckCategory,
    CheckSpec,
    Ecosystem,
    Finding,
    ResultState,
    Severity,
    VerificationClass,
)
from ..base import (
    ALL_SUPPORTED_ECOSYSTEMS,
    BaseCheck,
    CheckContext,
    blocked_finding,
    make_finding,
    parse_error_finding,
    read_known_text,
    static_evidence,
)

GIT_SPEC = CheckSpec(
    check_id="RC-GIT-001",
    name="Gitignore and generated artifact hygiene",
    category=CheckCategory.REPOSITORY,
    verification_class=VerificationClass.STATIC,
    description=(
        "Check for a readable .gitignore with common generated runtime, build, and "
        "coverage exclusions."
    ),
    severity=Severity.LOW,
    supported_ecosystems=ALL_SUPPORTED_ECOSYSTEMS,
    limitations=(
        "Git index state is not inspected.",
        "Pattern matching covers common entries and not every gitignore feature.",
    ),
    false_positive_policy=(
        "Require applicable generated-artifact patterns; do not infer tracked status "
        "from filesystem presence."
    ),
    scoring_group="repository-hygiene",
)


def _normalized_entries(text: str) -> tuple[str, ...]:
    entries: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        entries.append(line.rstrip("/"))
    return tuple(entries)


def _may_reinclude(pattern: str, artifact: str, *, directory: bool) -> bool:
    """Conservatively retain common exceptions to an otherwise accepted exclusion."""

    pattern = pattern.lstrip("/")
    if pattern.startswith("**/"):
        pattern = pattern[3:]
    if directory:
        return artifact in pattern.split("/") or fnmatchcase(artifact, pattern)
    return fnmatchcase(PurePosixPath(pattern).name, artifact) or fnmatchcase(artifact, pattern)


def _covers(entries: tuple[str, ...], expected: str, *, python: bool = False) -> bool:
    aliases = {expected}
    if expected == "*.pyc":
        aliases.update({"*.py[cod]", "*.py[co]"})
    elif expected == ".venv":
        aliases.update({"venv", "env", "ENV"})
    elif expected == "coverage" and python:
        aliases.update({".coverage", ".coverage.*", ".coverage*", "htmlcov", "coverage.xml"})

    for artifact in sorted(aliases):
        directory = artifact not in {
            "*.pyc",
            "*.py[cod]",
            "*.py[co]",
            ".coverage",
            ".coverage.*",
            ".coverage*",
            "coverage.xml",
        }
        forms = {artifact, f"**/{artifact}"}
        if directory:
            forms.update(
                {f"{artifact}/*", f"{artifact}/**", f"**/{artifact}/*", f"**/{artifact}/**"}
            )
        # Root anchoring is sufficient for root build/local directories, but not
        # for bytecode caches or node_modules that can occur throughout a tree.
        if expected not in {"*.pyc", "__pycache__", "node_modules"}:
            forms.update(f"/{form}" for form in tuple(forms) if not form.startswith("**/"))
        covered = False
        for entry in entries:
            if entry.startswith("!"):
                if _may_reinclude(entry[1:], artifact, directory=directory):
                    covered = False
            elif entry in forms:
                covered = True
        if covered:
            return True
    return False


class GitignoreHygieneCheck(BaseCheck):
    spec = GIT_SPEC

    def evaluate(self, context: CheckContext) -> tuple[Finding, ...]:
        gitignore = next(
            (
                repo_file.path
                for repo_file in context.discovery.files
                if not repo_file.is_symlink and repo_file.path == ".gitignore"
            ),
            None,
        )
        if gitignore is None:
            return (
                make_finding(
                    self.spec,
                    title=".gitignore is missing",
                    message="No repository-root .gitignore was discovered.",
                    assessment=Assessment.FAIL,
                    state=ResultState.STATIC_FAIL,
                    evidence=(static_evidence(self.spec, "No .gitignore was discovered."),),
                    remediation=(
                        "Add common runtime, build, coverage, and local-environment exclusions."
                    ),
                ),
            )
        result = read_known_text(context, gitignore)
        if result.status != "ok":
            if result.status == "inaccessible":
                return (
                    blocked_finding(
                        self.spec,
                        title=".gitignore is inaccessible",
                        message="The discovered .gitignore could not be read.",
                        reason=result.error or ".gitignore is inaccessible",
                        path=gitignore,
                    ),
                )
            return (
                parse_error_finding(
                    self.spec,
                    title=".gitignore cannot be assessed",
                    message="The discovered .gitignore could not be decoded as bounded text.",
                    reason=result.error or result.status,
                    path=gitignore,
                ),
            )
        entries = _normalized_entries(result.text)
        expected = {"dist", "build", "coverage"}
        python = Ecosystem.PYTHON in context.discovery.ecosystems
        if python:
            expected.update({"*.pyc", "__pycache__", ".venv", ".pytest_cache"})
        if Ecosystem.NODE in context.discovery.ecosystems:
            expected.add("node_modules")
        missing = tuple(
            sorted(item for item in expected if not _covers(entries, item, python=python))
        )
        if missing:
            return (
                make_finding(
                    self.spec,
                    title=".gitignore has incomplete generated-artifact hygiene",
                    message=(
                        "Common generated or local artifacts are not covered by the "
                        "repository .gitignore."
                    ),
                    assessment=Assessment.WARN,
                    state=ResultState.STATIC_FAIL,
                    evidence=(
                        static_evidence(
                            self.spec,
                            ".gitignore inspected for common generated-artifact patterns.",
                            source=gitignore,
                            details={"missing_patterns": list(missing)},
                        ),
                    ),
                    path=gitignore,
                    remediation=(
                        "Add applicable generated-artifact patterns without ignoring "
                        "source or lockfiles."
                    ),
                ),
            )
        return (
            make_finding(
                self.spec,
                title=".gitignore covers common generated artifacts",
                message=(
                    "The repository .gitignore contains applicable runtime, build, and "
                    "coverage exclusions."
                ),
                assessment=Assessment.PASS,
                state=ResultState.LOOKS_CORRECT,
                evidence=(
                    static_evidence(
                        self.spec, ".gitignore passed bounded hygiene checks.", source=gitignore
                    ),
                ),
            ),
        )


__all__ = ["GIT_SPEC", "GitignoreHygieneCheck"]
