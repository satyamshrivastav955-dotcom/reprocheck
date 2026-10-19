"""Applicability summary based on the existing safe discovery result."""

from __future__ import annotations

from ...models import (
    Assessment,
    CheckCategory,
    CheckSpec,
    Ecosystem,
    Finding,
    JSONValue,
    ResultState,
    Severity,
    VerificationClass,
)
from ..base import (
    ALL_SUPPORTED_ECOSYSTEMS,
    BaseCheck,
    CheckContext,
    make_finding,
    static_evidence,
)

DISCOVERY_SPEC = CheckSpec(
    check_id="RC-DISC-001",
    name="Ecosystem and applicability summary",
    category=CheckCategory.REPOSITORY,
    verification_class=VerificationClass.STATIC,
    description=(
        "Summarize ecosystems and discovered file coverage used to determine static "
        "check applicability."
    ),
    severity=Severity.INFO,
    supported_ecosystems=ALL_SUPPORTED_ECOSYSTEMS,
    limitations=(
        "Applicability follows discovery markers and does not infer framework semantics.",
    ),
    false_positive_policy=(
        "Unknown ecosystems are reported as unknown instead of being assigned a supported check."
    ),
    scoring_group="discovery",
)


class EcosystemApplicabilityCheck(BaseCheck):
    spec = DISCOVERY_SPEC

    def evaluate(self, context: CheckContext) -> tuple[Finding, ...]:
        try:
            root_exists = context.repository_root.resolve(strict=True).is_dir()
        except (OSError, RuntimeError):
            root_exists = False
        if not root_exists:
            evidence = static_evidence(
                self.spec, "Repository root is not accessible to static checks."
            )
            return (
                make_finding(
                    self.spec,
                    title="Repository applicability summary is blocked",
                    message="The configured repository root cannot be accessed.",
                    assessment=Assessment.BLOCKED,
                    state=ResultState.NOT_VERIFIED,
                    evidence=(evidence,),
                ),
            )

        ecosystems = tuple(context.discovery.ecosystems)
        known = tuple(ecosystem for ecosystem in ecosystems if ecosystem != Ecosystem.UNKNOWN)
        unknown = tuple(
            ecosystem.value for ecosystem in ecosystems if ecosystem == Ecosystem.UNKNOWN
        )
        details: dict[str, JSONValue] = {
            "ecosystems": [ecosystem.value for ecosystem in known],
            "unknown_ecosystems": list(unknown),
            "discovered_files": context.discovery.total_files,
            "ignored_files": context.discovery.ignored_files,
        }
        evidence = static_evidence(
            self.spec,
            "Safe repository discovery summary is available.",
            details=details,
        )
        message = (
            "Discovered ecosystems: " + ", ".join(ecosystem.value for ecosystem in known)
            if known
            else "No supported ecosystem marker was discovered."
        )
        if unknown:
            message += " Unknown ecosystem markers remain unclassified."
        return (
            make_finding(
                self.spec,
                title="Repository applicability summarized",
                message=message,
                assessment=Assessment.INFO,
                state=ResultState.LOOKS_CORRECT,
                evidence=(evidence,),
                ecosystems=known,
                applicability_reason="Applicability is derived from the supplied DiscoverySummary.",
            ),
        )


__all__ = ["DISCOVERY_SPEC", "EcosystemApplicabilityCheck"]
