"""Canonical evidence receipt generation, validation, and comparison."""

from .builder import build_receipt
from .canonical import (
    CANONICAL_HASH_KEY,
    canonical_bytes,
    canonical_hash,
    canonical_json,
    canonical_payload,
)
from .compare import ReceiptComparison, compare, compare_receipts
from .io import ReceiptFormatError, load_receipt, receipt_from_dict
from .validator import ReceiptValidation, assert_valid_receipt, is_valid_receipt, validate_receipt

__all__ = [
    "CANONICAL_HASH_KEY",
    "ReceiptComparison",
    "ReceiptValidation",
    "ReceiptFormatError",
    "assert_valid_receipt",
    "build_receipt",
    "canonical_bytes",
    "canonical_hash",
    "canonical_json",
    "canonical_payload",
    "compare",
    "compare_receipts",
    "is_valid_receipt",
    "load_receipt",
    "receipt_from_dict",
    "validate_receipt",
]
