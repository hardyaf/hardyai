from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AcceleratorLease:
    waiter_id: str
    resource_id: str
    lane: str
    priority: int
    fencing_token: int
    lease_expires_at: str


class AcceleratorAdmissionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = str(code)[:120]
        super().__init__(self.code)

