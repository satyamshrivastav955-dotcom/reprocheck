"""Static .gitignore and generated-artifact hygiene check."""

from __future__ import annotations

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


def _normalized_entries(text: str) -> set[str]:
    entries: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        entries.add(line.rstrip("/"))
    return entries


def _covers(entries: set[str], expected: str) -> bool:
    if expected in entries:
        return True
    if expected == "__pycache__":
        return any(entry in {"__pycache__", "**/__pycache__", "__pycache__/*"} for entry in entries)
    if expected == "node_modules":
        return any(
            entry in {"node_modules", "**/node_modules", "node_modules/*"} for entry in entries
        )
    if expected == "*.pyc":
        return any(entry in {"*.pyc", "**/*.pyc", "*.py[cod]"} for entry in entries)
    return any(
        entry == expected or entry == f"**/{expected}" or entry == f"{expected}/*"
        for entry in entries
    )


class GitignoreHygieneCheck(BaseCheck):
    spec = GIT_SPEC

    def evaluate(self, context: CheckContext) -> tuple[Finding, ...]:
        gitignore = next(
            (
                repo_file.path
                for repo_file in context.discovery.files
                if not repo_file.is_symlink and PurePosixPath(repo_file.path).name == ".gitignore"
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
        expected = {"dist", "build", "coverage", "*.pyc"}
        if Ecosystem.PYTHON in context.discovery.ecosystems:
            expected.update({"__pycache__", ".venv", ".pytest_cache"})
        if Ecosystem.NODE in context.discovery.ecosystems:
            expected.add("node_modules")
        missing = tuple(sorted(item for item in expected if not _covers(entries, item)))
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
