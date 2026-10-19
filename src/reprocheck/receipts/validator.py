"""Integrity and content validation for evidence receipts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..fingerprint import compute_content_fingerprint
from ..models import EvidenceReceipt
from .canonical import CANONICAL_HASH_KEY, canonical_hash


@dataclass(frozen=True)
class ReceiptValidation:
    """Structured validation result that is also usable as a boolean."""

    valid: bool
    errors: tuple[str, ...] = ()
    expected_hash: str | None = None
    actual_hash: str | None = None
    content_matches: bool | None = None

    def __bool__(self) -> bool:
        return self.valid


def validate_receipt(
    receipt: EvidenceReceipt,
    *,
    repository: Path | None = None,
) -> ReceiptValidation:
    """Recompute the canonical SHA-256 and optionally verify repository content."""

    errors: list[str] = []
    expected_value = receipt.integrity.details.get(CANONICAL_HASH_KEY)
    expected_hash = expected_value if isinstance(expected_value, str) else None
    actual_hash = canonical_hash(receipt)
    if expected_hash is None:
        errors.append(f"integrity.details.{CANONICAL_HASH_KEY} is missing")
    elif expected_hash != actual_hash:
        errors.append("canonical receipt SHA-256 does not match integrity metadata")

    if not receipt.integrity.verified:
        errors.append("integrity metadata is not marked verified")
    if receipt.integrity.content_fingerprint != receipt.content:
        errors.append("integrity content fingerprint does not match receipt content")

    content_matches: bool | None = None
    if repository is not None:
        try:
            actual_content = compute_content_fingerprint(repository)
        except (OSError, RuntimeError, ValueError) as error:
            errors.append(f"repository content fingerprint could not be recomputed: {error}")
        else:
            content_matches = actual_content == receipt.content
            if not content_matches:
                errors.append("repository content fingerprint does not match receipt content")

    return ReceiptValidation(
        valid=not errors,
        errors=tuple(errors),
        expected_hash=expected_hash,
        actual_hash=actual_hash,
        content_matches=content_matches,
    )


def assert_valid_receipt(receipt: EvidenceReceipt, *, repository: Path | None = None) -> None:
    """Raise ``ValueError`` when a receipt fails integrity validation."""

    result = validate_receipt(receipt, repository=repository)
    if not result.valid:
        raise ValueError("invalid evidence receipt: " + "; ".join(result.errors))


is_valid_receipt = validate_receipt


__all__ = ["ReceiptValidation", "assert_valid_receipt", "is_valid_receipt", "validate_receipt"]
