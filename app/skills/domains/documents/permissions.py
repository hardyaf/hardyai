from __future__ import annotations

from app.skills.domains.documents.types import DocumentRecord, ProcessingState, Sensitivity


class DocumentRequestAccessPolicy:
    """Trusted transport scope for Core-to-Documents interactive calls."""

    _DISCORD_OPERATIONS = frozenset(
        {
            "documents.status",
            "documents.get",
            "documents.escalate_ocr",
            "documents.correct_field",
            "documents.confirm_fields",
            "documents.queue_processing",
            "documents.review_field",
        }
    )

    @staticmethod
    def _principal_kind(context: dict[str, object]) -> str:
        return str(context.get("principal_kind") or "").strip().casefold()

    @staticmethod
    def _source(context: dict[str, object]) -> str:
        return str(
            context.get("source")
            or context.get("request_source")
            or context.get("source_interface")
            or "dashboard"
        ).strip().casefold()

    @staticmethod
    def _bounded_ids(context: dict[str, object], key: str) -> tuple[str, ...]:
        raw = context.get(key)
        if not isinstance(raw, (list, tuple)):
            return ()
        return tuple(
            dict.fromkeys(
                str(item).strip()
                for item in raw[:4]
                if isinstance(item, str) and str(item).strip()
            )
        )

    @classmethod
    def discord_document_ids(cls, context: dict[str, object]) -> frozenset[str]:
        current = cls._bounded_ids(context, "current_document_attachment_ids")
        if current:
            return frozenset(current)
        return frozenset(cls._bounded_ids(context, "document_attachment_ids"))

    @classmethod
    def authorized(cls, context: dict[str, object]) -> bool:
        principal_kind = cls._principal_kind(context)
        source = cls._source(context)
        if principal_kind in {"operator", "test"} and source in {"dashboard", "web", "test"}:
            return True
        return (
            principal_kind == "discord_adapter"
            and source == "discord"
            and bool(cls.discord_document_ids(context))
        )

    @classmethod
    def operation_authorized(
        cls,
        *,
        operation: str,
        document_id: str,
        context: dict[str, object],
    ) -> bool:
        if cls._principal_kind(context) != "discord_adapter":
            return cls.authorized(context)
        return (
            str(operation or "").strip().casefold() in cls._DISCORD_OPERATIONS
            and bool(str(document_id or "").strip())
            and str(document_id).strip() in cls.discord_document_ids(context)
        )

    @classmethod
    def resolve_document_id(
        cls,
        *,
        operation: str,
        requested_document_id: str | None,
        context: dict[str, object],
    ) -> str | None:
        requested = str(requested_document_id or "").strip()
        if cls._principal_kind(context) != "discord_adapter":
            return requested or None
        allowed = cls.discord_document_ids(context)
        if requested:
            return requested if requested in allowed else None
        if len(allowed) != 1:
            return None
        candidate = next(iter(allowed))
        return (
            candidate
            if cls.operation_authorized(
                operation=operation,
                document_id=candidate,
                context=context,
            )
            else None
        )


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
