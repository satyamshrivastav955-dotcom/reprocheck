"""Deterministic registration for static audit checks."""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..models import CheckSpec, Finding, VerificationClass
from .base import Check, CheckContext

_CHECK_ID_RE = re.compile(r"^RC-[A-Z0-9]+-\d{3}$")


class CheckRegistry:
    """A duplicate-safe registry whose iteration order is check-ID sorted."""

    def __init__(self, checks: Iterable[Check] = ()) -> None:
        self._checks: dict[str, Check] = {}
        self.register_many(checks)

    @staticmethod
    def _validate(check: Check) -> CheckSpec:
        spec = getattr(check, "spec", None)
        if not isinstance(spec, CheckSpec):
            raise TypeError("registered check must expose a CheckSpec as spec")
        if not _CHECK_ID_RE.fullmatch(spec.check_id):
            raise ValueError(f"invalid stable check ID: {spec.check_id!r}")
        if spec.verification_class is not VerificationClass.STATIC:
            raise ValueError(f"check {spec.check_id} is not a static check")
        evaluate = getattr(check, "evaluate", None)
        if not callable(evaluate):
            raise TypeError(f"check {spec.check_id} does not expose evaluate(context)")
        return spec

    def register(self, check: Check) -> None:
        """Register one check, rejecting invalid or duplicate stable IDs."""

        spec = self._validate(check)
        if spec.check_id in self._checks:
            raise ValueError(f"check ID already registered: {spec.check_id}")
        self._checks[spec.check_id] = check

    def register_many(self, checks: Iterable[Check]) -> None:
        """Validate a batch before mutating the registry."""

        pending: list[tuple[str, Check]] = []
        seen: set[str] = set()
        for check in checks:
            spec = self._validate(check)
            if spec.check_id in self._checks or spec.check_id in seen:
                raise ValueError(f"check ID already registered: {spec.check_id}")
            seen.add(spec.check_id)
            pending.append((spec.check_id, check))
        self._checks.update(pending)

    def get(self, check_id: str) -> Check | None:
        """Return one registered check by stable ID."""

        return self._checks.get(check_id)

    @property
    def checks(self) -> tuple[Check, ...]:
        """Return checks in deterministic stable-ID order."""

        return tuple(self._checks[check_id] for check_id in sorted(self._checks))

    @property
    def specs(self) -> tuple[CheckSpec, ...]:
        """Return registered metadata in deterministic stable-ID order."""

        return tuple(check.spec for check in self.checks)

    def evaluate(self, context: CheckContext) -> tuple[Finding, ...]:
        """Evaluate all registered checks in deterministic order."""

        findings: list[Finding] = []
        for check in self.checks:
            findings.extend(check.evaluate(context))
        return tuple(findings)


def default_registry() -> CheckRegistry:
    """Build a fresh registry containing all built-in static checks."""

    from .modules import BUILTIN_CHECKS

    return CheckRegistry(BUILTIN_CHECKS)


__all__ = ["CheckRegistry", "default_registry"]
