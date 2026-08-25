from __future__ import annotations

from app.skills.domains.documents.types import DocumentRecord


class DocumentAccessPolicy:
    """Phase 1 policy: private owner access only; no sharing or inherited permissions."""

    @staticmethod
    def can_read(*, record: DocumentRecord, user_id: str) -> bool:
        return bool(user_id) and record.owner_id == user_id
