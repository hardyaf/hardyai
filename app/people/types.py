from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ContactCandidate:
    contact_ref: str
    display_name: str
    organization: str | None = None
    emails: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()


class ContactProvider(Protocol):
    """Read/write authority selected by ADR before execution can be enabled."""

    provider_name: str

    def search(self, *, query: str, limit: int) -> tuple[ContactCandidate, ...]: ...

    def upsert(
        self,
        *,
        contact_ref: str | None,
        fields: dict[str, str],
        operation_id: str,
    ) -> ContactCandidate: ...
