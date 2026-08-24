from __future__ import annotations

from typing import Any, Protocol

from app.tickets.types import SourceObservation


class SourceOfTruthVerifier(Protocol):
    name: str
    version: str

    def observe(
        self,
        *,
        resource_locator: dict[str, Any],
        expected_state: dict[str, Any],
        operation_receipt: dict[str, Any],
    ) -> SourceObservation:
        ...


class VerifierRegistry:
    def __init__(self) -> None:
        self._verifiers: dict[tuple[str, str], SourceOfTruthVerifier] = {}

    def register(self, verifier: SourceOfTruthVerifier) -> None:
        key = (verifier.name.strip().lower(), verifier.version.strip())
        if not key[0] or not key[1]:
            raise ValueError("Verifier name and version are required.")
        if key in self._verifiers:
            raise ValueError(f"Verifier already registered: {key[0]}@{key[1]}")
        self._verifiers[key] = verifier

    def get(self, *, name: str, version: str) -> SourceOfTruthVerifier | None:
        return self._verifiers.get((name.strip().lower(), version.strip()))

    def require(self, *, name: str, version: str) -> SourceOfTruthVerifier:
        verifier = self.get(name=name, version=version)
        if verifier is None:
            raise LookupError(f"Unknown trusted verifier: {name}@{version}")
        return verifier

    def inventory(self) -> list[dict[str, str]]:
        return [
            {"name": name, "version": version}
            for name, version in sorted(self._verifiers)
        ]
