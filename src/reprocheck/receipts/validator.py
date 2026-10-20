"""Integrity and content validation for evidence receipts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..fingerprint import compute_content_fingerprint
from ..models import ContentFingerprint, EvidenceReceipt, IntegrityMetadata
from .canonical import CANONICAL_HASH_KEY, canonical_hash

_HEX_DIGITS = frozenset("0123456789abcdef")


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX_DIGITS for character in value)
    )


def _valid_content_fingerprint(value: Any) -> bool:
    return (
        isinstance(value, ContentFingerprint)
        and isinstance(value.algorithm, str)
        and isinstance(value.digest, str)
        and (value.file_count is None or type(value.file_count) is int)
    )


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
    expected_hash: str | None = None
    if not _valid_content_fingerprint(receipt.content):
        errors.append("receipt content fingerprint is invalid")

    integrity = receipt.integrity
    if not isinstance(integrity, IntegrityMetadata):
        errors.append("integrity metadata is invalid")
        integrity_details: Any = None
    else:
        integrity_details = integrity.details

    expected_value = (
        integrity_details.get(CANONICAL_HASH_KEY) if isinstance(integrity_details, dict) else None
    )
    if expected_value is None:
        errors.append(f"integrity.details.{CANONICAL_HASH_KEY} is missing")
    elif not _valid_sha256(expected_value):
        errors.append(f"integrity.details.{CANONICAL_HASH_KEY} is invalid")
    else:
        expected_hash = expected_value

    actual_hash: str | None
    try:
        actual_hash = canonical_hash(receipt)
    except (TypeError, ValueError):
        actual_hash = None
        errors.append("receipt cannot be canonicalized")
    if expected_hash is not None and actual_hash is not None and expected_hash != actual_hash:
        errors.append("canonical receipt SHA-256 does not match integrity metadata")

    if isinstance(integrity, IntegrityMetadata) and type(integrity.verified) is not bool:
        errors.append("integrity.verified must be a boolean")
    elif isinstance(integrity, IntegrityMetadata) and not integrity.verified:
        errors.append("integrity metadata is not marked verified")

    if isinstance(integrity, IntegrityMetadata):
        if integrity.content_fingerprint is None:
            errors.append("integrity content fingerprint is missing")
        elif not _valid_content_fingerprint(integrity.content_fingerprint):
            errors.append("integrity content fingerprint is invalid")
        elif integrity.content_fingerprint != receipt.content:
            errors.append("integrity content fingerprint does not match receipt content")
        if integrity.method is not None and not isinstance(integrity.method, str):
            errors.append("integrity.method must be a string or null")
        if not isinstance(integrity.details, dict):
            errors.append("integrity.details must be an object")

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
