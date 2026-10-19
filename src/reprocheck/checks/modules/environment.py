"""Static environment template and documentation check."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from ...models import (
    Assessment,
    CheckCategory,
    CheckSpec,
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

ENV_SPEC = CheckSpec(
    check_id="RC-ENV-001",
    name="Environment template or documentation",
    category=CheckCategory.DOCUMENTATION,
    verification_class=VerificationClass.STATIC,
    description=(
        "Find a non-secret environment template or conservative documentation of "
        "environment configuration."
    ),
    severity=Severity.LOW,
    supported_ecosystems=ALL_SUPPORTED_ECOSYSTEMS,
    limitations=(
        "The check does not infer required variables from application code.",
        "It does not validate values or connectivity.",
    ),
    false_positive_policy=(
        "Only known template names or explicit environment/.env documentation satisfy the check."
    ),
    scoring_group="environment",
)

_TEMPLATE_NAMES = {".env.example", ".env.template", ".env.sample", "env.example", "env.template"}
_DOCUMENTATION_RE = re.compile(
    r"(?i)(?:environment\s+variables?|configuration\s+variables?|\.env\b)"
)
_VARIABLE_RE = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b\s*(?:=|:)")


def _readme_paths(context: CheckContext) -> tuple[str, ...]:
    return tuple(
        sorted(
            repo_file.path
            for repo_file in context.discovery.files
            if not repo_file.is_symlink
            and PurePosixPath(repo_file.path).name.lower()
            in {"readme", "readme.md", "readme.rst", "readme.txt"}
        )
    )


class EnvironmentDocumentationCheck(BaseCheck):
    spec = ENV_SPEC

    def evaluate(self, context: CheckContext) -> tuple[Finding, ...]:
        template_paths: list[str] = []
        for repo_file in context.discovery.files:
            if repo_file.is_symlink or len(PurePosixPath(repo_file.path).parts) != 1:
                continue
            if PurePosixPath(repo_file.path).name.lower() in _TEMPLATE_NAMES:
                template_paths.append(repo_file.path)
        if template_paths:
            path = sorted(template_paths)[0]
            result = read_known_text(context, path)
            if result.status != "ok":
                if result.status == "inaccessible":
                    return (
                        blocked_finding(
                            self.spec,
                            title="Environment template is inaccessible",
                            message=f"The environment template {path} could not be read.",
                            reason=result.error or "template is inaccessible",
                            path=path,
                        ),
                    )
                return (
                    parse_error_finding(
                        self.spec,
                        title="Environment template cannot be assessed",
                        message=f"The environment template {path} could not be read safely.",
                        reason=result.error or result.status,
                        path=path,
                    ),
                )
            return (
                make_finding(
                    self.spec,
                    title="Environment template detected",
                    message="A conventional non-secret environment template is present.",
                    assessment=Assessment.INFO,
                    state=ResultState.PRESENT,
                    evidence=(
                        static_evidence(self.spec, "Environment template detected.", source=path),
                    ),
                ),
            )

        for path in _readme_paths(context):
            result = read_known_text(context, path)
            if result.status != "ok":
                if result.status == "inaccessible":
                    return (
                        blocked_finding(
                            self.spec,
                            title="Environment documentation is blocked",
                            message=f"README environment documentation {path} could not be read.",
                            reason=result.error or "README is inaccessible",
                            path=path,
                        ),
                    )
                return (
                    parse_error_finding(
                        self.spec,
                        title="Environment documentation cannot be assessed",
                        message=(
                            f"README environment documentation {path} could not be read safely."
                        ),
                        reason=result.error or result.status,
                        path=path,
                    ),
                )
            if _DOCUMENTATION_RE.search(result.text) and (
                ".env" in result.text.lower() or _VARIABLE_RE.search(result.text)
            ):
                return (
                    make_finding(
                        self.spec,
                        title="Environment configuration is documented",
                        message=(
                            "README text contains a conservative environment "
                            "configuration reference."
                        ),
                        assessment=Assessment.PASS,
                        state=ResultState.LOOKS_CORRECT,
                        evidence=(
                            static_evidence(
                                self.spec,
                                "Environment configuration documentation detected.",
                                source=path,
                            ),
                        ),
                    ),
                )
        return (
            make_finding(
                self.spec,
                title="Environment template or documentation is missing",
                message=(
                    "No conventional environment template or explicit environment "
                    "documentation was discovered."
                ),
                assessment=Assessment.FAIL,
                state=ResultState.STATIC_FAIL,
                evidence=(
                    static_evidence(
                        self.spec, "No environment template or documentation was discovered."
                    ),
                ),
                remediation=(
                    "Commit a redacted .env.example or document required environment variables."
                ),
            ),
        )


__all__ = ["ENV_SPEC", "EnvironmentDocumentationCheck"]
