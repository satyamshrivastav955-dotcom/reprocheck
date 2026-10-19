"""Conservative machine-path and likely-secret static scans."""

from __future__ import annotations

import re

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
    text_file_paths,
)

PATH_SPEC = CheckSpec(
    check_id="RC-PATH-001",
    name="Suspicious machine-specific absolute paths",
    category=CheckCategory.INTEGRITY,
    verification_class=VerificationClass.STATIC,
    description="Scan bounded text files for common developer-machine absolute path patterns.",
    severity=Severity.MEDIUM,
    supported_ecosystems=ALL_SUPPORTED_ECOSYSTEMS,
    limitations=(
        "Only high-signal Unix and Windows user/workspace path forms are reported.",
        "The scan cannot determine whether a path is intentional.",
    ),
    false_positive_policy=(
        "Report a warning with redacted evidence; do not report generic URLs or "
        "ordinary relative paths."
    ),
    scoring_group="portability",
)


SECURITY_SPEC = CheckSpec(
    check_id="RC-SEC-001",
    name="Likely secret patterns",
    category=CheckCategory.INTEGRITY,
    verification_class=VerificationClass.STATIC,
    description=(
        "Scan bounded text files for high-confidence private keys and non-placeholder "
        "secret assignments."
    ),
    severity=Severity.HIGH,
    supported_ecosystems=ALL_SUPPORTED_ECOSYSTEMS,
    limitations=(
        "This is a conservative pattern scan, not a complete secret scanner.",
        "Values are never included in evidence.",
    ),
    false_positive_policy=(
        "Require high-confidence token shapes or private-key blocks and redact all matched values."
    ),
    scoring_group="security",
)

_MACHINE_PATH_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:"
    r"/(?:home|users|mnt|private/var|workspace|workspaces|tmp)/[^\s\"'`]+|"
    r"[A-Z]:\\(?:Users|Documents and Settings|workspaces?)\\[^\s\"']+"
    r")"
)
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [^-\n]*PRIVATE KEY-----")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret(?:[_-]?key)?|password|private[_-]?key)\b\s*[:=]\s*[\"']?([A-Za-z0-9_+/=-]{20,})"
)
_KNOWN_TOKEN_RE = re.compile(
    r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{20,})\b"
)
_PLACEHOLDER_RE = re.compile(
    r"(?i)(?:example|sample|changeme|change-me|your[_-]?value|placeholder|redacted|<[^>]+>)"
)


def _first_match(text: str, pattern: re.Pattern[str]) -> tuple[int, str] | None:
    match = pattern.search(text)
    if match is None:
        return None
    return text.count("\n", 0, match.start()) + 1, match.group(0)


class SuspiciousPathCheck(BaseCheck):
    spec = PATH_SPEC

    def evaluate(self, context: CheckContext) -> tuple[Finding, ...]:
        scanned = 0
        for path in text_file_paths(context):
            result = read_known_text(context, path)
            if result.status == "inaccessible":
                return (
                    blocked_finding(
                        self.spec,
                        title="Absolute-path scan is blocked",
                        message=f"Static path scanning could not read {path}.",
                        reason=result.error or "file is inaccessible",
                        path=path,
                    ),
                )
            if result.status == "malformed":
                return (
                    parse_error_finding(
                        self.spec,
                        title="Absolute-path scan encountered malformed text",
                        message=f"Static path scanning could not decode {path}.",
                        reason=result.error or "file is malformed",
                        path=path,
                    ),
                )
            if result.status != "ok":
                continue
            scanned += 1
            match = _first_match(result.text, _MACHINE_PATH_RE)
            if match is not None:
                line, value = match
                return (
                    make_finding(
                        self.spec,
                        title="Machine-specific absolute path detected",
                        message=(
                            "A bounded text scan found a likely developer-machine absolute path."
                        ),
                        assessment=Assessment.WARN,
                        state=ResultState.STATIC_FAIL,
                        evidence=(
                            static_evidence(
                                self.spec,
                                "Suspicious absolute path matched; excerpt redacted.",
                                source=path,
                                excerpt=value,
                                details={"scanned_text_files": scanned},
                            ),
                        ),
                        path=path,
                        line=line,
                        remediation=(
                            "Use repository-relative paths, environment variables, or "
                            "tool configuration placeholders."
                        ),
                    ),
                )
        return (
            make_finding(
                self.spec,
                title="No suspicious machine-specific absolute paths found",
                message=(
                    "Bounded text scanning found no high-signal machine-specific "
                    "absolute path patterns."
                ),
                assessment=Assessment.PASS,
                state=ResultState.LOOKS_CORRECT,
                evidence=(
                    static_evidence(
                        self.spec,
                        "Bounded absolute-path scan completed.",
                        details={"scanned_text_files": scanned},
                    ),
                ),
            ),
        )


class LikelySecretCheck(BaseCheck):
    spec = SECURITY_SPEC

    def evaluate(self, context: CheckContext) -> tuple[Finding, ...]:
        scanned = 0
        for path in text_file_paths(context):
            result = read_known_text(context, path)
            if result.status == "inaccessible":
                return (
                    blocked_finding(
                        self.spec,
                        title="Secret-pattern scan is blocked",
                        message=f"Static secret scanning could not read {path}.",
                        reason=result.error or "file is inaccessible",
                        path=path,
                    ),
                )
            if result.status == "malformed":
                return (
                    parse_error_finding(
                        self.spec,
                        title="Secret-pattern scan encountered malformed text",
                        message=f"Static secret scanning could not decode {path}.",
                        reason=result.error or "file is malformed",
                        path=path,
                    ),
                )
            if result.status != "ok":
                continue
            scanned += 1
            private_key = _first_match(result.text, _PRIVATE_KEY_RE)
            assignment = _first_match(result.text, _SECRET_ASSIGNMENT_RE)
            known_token = _first_match(result.text, _KNOWN_TOKEN_RE)
            match = private_key or known_token
            if (
                match is None
                and assignment is not None
                and not _PLACEHOLDER_RE.search(assignment[1])
            ):
                match = assignment
            if match is not None:
                line, value = match
                return (
                    make_finding(
                        self.spec,
                        title="Likely secret pattern detected",
                        message=(
                            "A high-confidence secret pattern was found; evidence has "
                            "been redacted."
                        ),
                        assessment=Assessment.FAIL,
                        state=ResultState.STATIC_FAIL,
                        evidence=(
                            static_evidence(
                                self.spec,
                                "Likely secret pattern matched; value redacted.",
                                source=path,
                                excerpt=value,
                                details={"scanned_text_files": scanned, "redacted": True},
                            ),
                        ),
                        path=path,
                        line=line,
                        remediation=(
                            "Remove the value from repository content and rotate it "
                            "through the appropriate secret store."
                        ),
                    ),
                )
        return (
            make_finding(
                self.spec,
                title="No likely secret patterns found",
                message=(
                    "Bounded text scanning found no high-confidence private-key or "
                    "secret-assignment pattern."
                ),
                assessment=Assessment.PASS,
                state=ResultState.LOOKS_CORRECT,
                evidence=(
                    static_evidence(
                        self.spec,
                        "Bounded secret-pattern scan completed.",
                        details={"scanned_text_files": scanned},
                    ),
                ),
            ),
        )


__all__ = ["LikelySecretCheck", "PATH_SPEC", "SECURITY_SPEC", "SuspiciousPathCheck"]
