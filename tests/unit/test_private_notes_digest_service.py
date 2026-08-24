from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.skills.domains.private_notes.service import (
    PrivateNotesChannelConfig,
    PrivateNotesDigestCompiler,
    PrivateNotesDigestService,
    split_discord_message,
)
from app.skills.domains.private_notes.storage import PrivateNotesSQLiteStorage


class RecordingConversationBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def respond(self, text: str, context: dict[str, object] | None = None) -> str:
        self.calls.append({"text": text, "context": dict(context or {})})
        return "**To-dos**\n- Maybe call Kelly.\n\n**Ideas**\n- Try the blue layout."


def _config() -> PrivateNotesChannelConfig:
    return PrivateNotesChannelConfig.from_mapping(
        {
            "guild_id": "100",
            "channel_id": "200",
            "delivery_channel_id": "200",
            "allowed_user_ids": ["300"],
            "owner_user_id": "taylor",
            "owner_display_name": "Taylor",
            "agent_id": "catparty",
            "timezone": "America/New_York",
            "digest_at": "18:00",
            "skip_if_empty": True,
            "raw_note_retention_days": 30,
        }
    )


def test_private_notes_capture_is_silent_idempotent_and_digest_is_once_per_local_day(tmp_path):
    storage = PrivateNotesSQLiteStorage(str(tmp_path / "private-notes.db"))
    backend = RecordingConversationBackend()
    service = PrivateNotesDigestService(
        storage=storage,
        compiler=PrivateNotesDigestCompiler(conversation_backend=backend),
    )
    config = _config()
    local_tz = ZoneInfo("America/New_York")

    try:
        captured = service.capture_note(
            config=config,
            external_message_id="discord:message-1",
            author_external_user_id="300",
            author_display_name="Taylor",
            content="maybe call Kelly and try the blue layout",
            captured_at=datetime(2026, 8, 16, 10, 0, tzinfo=local_tz),
        )
        duplicate = service.capture_note(
            config=config,
            external_message_id="discord:message-1",
            author_external_user_id="300",
            author_display_name="Taylor",
            content="a duplicate delivery",
            captured_at=datetime(2026, 8, 16, 10, 1, tzinfo=local_tz),
        )
        ignored = service.capture_note(
            config=config,
            external_message_id="discord:message-2",
            author_external_user_id="999",
            author_display_name="Someone Else",
            content="do not store this",
        )

        assert captured["status"] == "captured"
        assert duplicate["status"] == "duplicate"
        assert ignored == {"status": "ignored", "reason": "author_not_allowed"}
        assert service.pending_note_count(config=config) == 1
        assert service.prepare_due_digest(
            config=config,
            now=datetime(2026, 8, 16, 17, 59, tzinfo=local_tz),
        ) is None

        prepared = service.prepare_due_digest(
            config=config,
            now=datetime(2026, 8, 16, 18, 0, tzinfo=local_tz),
        )
        assert prepared is not None
        assert prepared["delivery_channel_id"] == "200"
        assert prepared["discord_message_ids"] == []
        assert "Evening notes" in prepared["parts"][0]
        assert "BEGIN UNTRUSTED NOTES JSON" in str(backend.calls[0]["text"])
        assert backend.calls[0]["context"] == {
            "agent_id": "catparty",
            "requested_by_user_id": "taylor",
            "micro_intent": "private_notes.compile_digest",
            "runtime_skill_intents": ["private_notes.compile_digest"],
            "web_research": None,
        }

        service.record_delivery_part(digest_id=prepared["digest_id"], message_id="discord-output-1")
        resumed = service.prepare_due_digest(
            config=config,
            now=datetime(2026, 8, 16, 18, 1, tzinfo=local_tz),
        )
        assert resumed is not None
        assert resumed["discord_message_ids"] == ["discord-output-1"]

        service.mark_delivered(digest_id=prepared["digest_id"])
        assert service.prepare_due_digest(
            config=config,
            now=datetime(2026, 8, 16, 18, 2, tzinfo=local_tz),
        ) is None
        assert service.pending_note_count(config=config) == 0
        digest = storage.get_digest(channel_id="200", local_date="2026-08-16")
        assert digest is not None
        assert digest["status"] == "delivered"
    finally:
        storage.close()


def test_failed_digest_retries_are_bounded_and_notes_return_to_pending(tmp_path):
    storage = PrivateNotesSQLiteStorage(str(tmp_path / "private-notes-failure.db"))
    service = PrivateNotesDigestService(
        storage=storage,
        compiler=PrivateNotesDigestCompiler(conversation_backend=None),
    )
    config = _config()
    local_tz = ZoneInfo("America/New_York")
    try:
        service.capture_note(
            config=config,
            external_message_id="discord:retry-note",
            author_external_user_id="300",
            author_display_name="Taylor",
            content="remember the garden idea",
        )
        prepared = service.prepare_due_digest(
            config=config,
            now=datetime(2026, 8, 16, 18, 0, tzinfo=local_tz),
        )
        assert prepared is not None
        status = None
        for attempt in range(service.MAX_DELIVERY_ATTEMPTS):
            result = service.mark_delivery_failed(
                digest_id=prepared["digest_id"],
                error=f"failed {attempt}",
            )
            status = result["status"]
        assert status == "dead_letter"
        assert service.pending_note_count(config=config) == 1
        assert service.prepare_due_digest(
            config=config,
            now=datetime(2026, 8, 16, 18, 5, tzinfo=local_tz),
        ) is None
    finally:
        storage.close()


def test_retention_deletes_only_old_digested_notes_and_keeps_digest_summary(tmp_path):
    storage = PrivateNotesSQLiteStorage(str(tmp_path / "private-notes-retention.db"))
    service = PrivateNotesDigestService(
        storage=storage,
        compiler=PrivateNotesDigestCompiler(conversation_backend=None),
    )
    config = _config()
    local_tz = ZoneInfo("America/New_York")
    try:
        service.capture_note(
            config=config,
            external_message_id="discord:old-digested",
            author_external_user_id="300",
            author_display_name="Taylor",
            content="already summarized",
            captured_at=datetime(2026, 1, 1, 10, 0, tzinfo=local_tz),
        )
        prepared = service.prepare_due_digest(
            config=config,
            now=datetime(2026, 1, 1, 18, 0, tzinfo=local_tz),
        )
        assert prepared is not None
        service.mark_delivered(digest_id=prepared["digest_id"])

        service.capture_note(
            config=config,
            external_message_id="discord:old-pending",
            author_external_user_id="300",
            author_display_name="Taylor",
            content="must remain pending",
            captured_at=datetime(2026, 1, 2, 10, 0, tzinfo=local_tz),
        )

        deleted = service.enforce_retention(
            config=config,
            now=datetime(2026, 2, 16, 18, 0, tzinfo=local_tz),
        )

        assert deleted == 1
        assert service.pending_note_count(config=config) == 1
        digest = storage.get_digest(channel_id="200", local_date="2026-01-01")
        assert digest is not None
        assert digest["status"] == "delivered"
        assert "already summarized" in str(digest["summary_text"])
        assert service.enforce_retention(
            config=config,
            now=datetime(2026, 2, 16, 18, 1, tzinfo=local_tz),
        ) == 0
    finally:
        storage.close()


def test_private_notes_retention_days_are_bounded():
    mapping = {
        "guild_id": "100",
        "channel_id": "200",
        "allowed_user_ids": ["300"],
        "owner_user_id": "taylor",
    }
    assert PrivateNotesChannelConfig.from_mapping(mapping).raw_note_retention_days == 30
    for invalid in (0, 3651, "not-a-number"):
        try:
            PrivateNotesChannelConfig.from_mapping(
                {**mapping, "raw_note_retention_days": invalid}
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected invalid retention value to fail: {invalid!r}")


def test_split_discord_message_is_bounded():
    parts = split_discord_message("Paragraph one.\n\n" + ("word " * 900), max_chars=400, max_parts=4)
    assert len(parts) == 4
    assert all(len(part) <= 400 for part in parts)
    assert parts[-1].endswith("[Digest shortened; remaining notes stay queued for a later digest.]")
