"""Stable receipt canonicalization and SHA-256 integrity hashing."""

from __future__ import annotations

from hashlib import sha256

from ..models import EvidenceReceipt, JSONValue, dumps_json, to_json_dict

CANONICAL_HASH_KEY = "canonical_sha256"
_TEMPORAL_KEYS = frozenset(
    {
        "created_at",
        "finished_at",
        "generated_at",
        "observed_at",
        "started_at",
        "timestamp",
        "updated_at",
    }
)


def _without_temporal_or_integrity_hash(
    value: JSONValue, *, in_integrity: bool = False
) -> JSONValue:
    if isinstance(value, dict):
        result: dict[str, JSONValue] = {}
        for key, item in value.items():
            if key.lower() in _TEMPORAL_KEYS:
                continue
            if in_integrity and key in {
                CANONICAL_HASH_KEY,
                "receipt_sha256",
                "integrity_sha256",
            }:
                continue
            result[key] = _without_temporal_or_integrity_hash(
                item,
                in_integrity=in_integrity or key == "integrity",
            )
        return result
    if isinstance(value, list):
        return [
            _without_temporal_or_integrity_hash(item, in_integrity=in_integrity) for item in value
        ]
    return value


def canonical_payload(receipt: EvidenceReceipt) -> dict[str, JSONValue]:
    """Return the receipt payload used for integrity and comparison hashing."""

    value = _without_temporal_or_integrity_hash(to_json_dict(receipt))
    if not isinstance(value, dict):  # pragma: no cover - model contract guard
        raise TypeError("an evidence receipt must serialize to an object")
    return value


def canonical_json(receipt: EvidenceReceipt) -> str:
    """Serialize a receipt with sorted keys and timestamps removed."""

    return dumps_json(canonical_payload(receipt))


def canonical_bytes(receipt: EvidenceReceipt) -> bytes:
    """Return UTF-8 canonical receipt bytes."""

    return canonical_json(receipt).encode("utf-8")


def canonical_hash(receipt: EvidenceReceipt) -> str:
    """Return the lowercase SHA-256 of the canonical receipt payload."""

    return sha256(canonical_bytes(receipt)).hexdigest()


canonicalize_receipt = canonical_payload


__all__ = [
    "CANONICAL_HASH_KEY",
    "canonical_bytes",
    "canonicalize_receipt",
    "canonical_hash",
    "canonical_json",
    "canonical_payload",
]
