from __future__ import annotations

from app.skills.domains.documents.types import DocumentRecord, ProcessingState, Sensitivity


class DocumentAccessPolicy:
    """Phase 1 policy: private owner access only; no sharing or inherited permissions."""

    @staticmethod
    def can_read(*, record: DocumentRecord, user_id: str) -> bool:
        return bool(user_id) and record.owner_id == user_id

    @classmethod
    def can_read_fields(cls, *, record: DocumentRecord, user_id: str) -> bool:
        return cls.can_read(record=record, user_id=user_id) and record.sensitivity not in {
            Sensitivity.IDENTITY,
            Sensitivity.HIGHLY_RESTRICTED,
        } and record.processing_state != ProcessingState.PROTECTED_PENDING

    @classmethod
    def can_read_archive_text(cls, *, record: DocumentRecord, user_id: str) -> bool:
        return cls.can_read_fields(record=record, user_id=user_id) and record.archive_text_visible

    @classmethod
    def can_read_source(cls, *, record: DocumentRecord, user_id: str) -> bool:
        return cls.can_read_archive_text(record=record, user_id=user_id)
