from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.jobs.document_completion import (
    DOCUMENT_DISCORD_COMPLETION_JOB,
    DurableDocumentCompletionEnqueuer,
)
from app.jobs.repository import DurableJobRepository
from app.services.discord.bot import (
    DiscordJarvisBot,
    discord_document_response_allowed,
    load_discord_permissions_policy,
)
from app.services.document_completion_service import DocumentCompletionNotificationService
from app.integrations.discord_attachment.types import DiscordAttachmentReceipt


class DocumentsStub:
    def __init__(self, *, processing_state: str, message: str = "OCR finished.") -> None:
        self.processing_state = processing_state
        self.message = message
        self.contexts: list[dict] = []

    def execute(self, *, intent, entities, context):
        self.contexts.append(dict(context))
        assert intent == "documents.get"
        assert entities["document_id"] == "doc-1"
        return {
            "status": "ok",
            "message": self.message,
            "document": {
                "document_id": "doc-1",
                "state": "ready",
                "processing_state": self.processing_state,
            },
        }


def _register(enqueuer: DurableDocumentCompletionEnqueuer):
    return enqueuer.register_discord(
        document_id="doc-1",
        guild_id="100",
        channel_id="200",
        user_id="300",
        message_id="400",
        attachment_id="500",
    )


def _permissions(tmp_path, *, response_channel: int = 200):
    path = tmp_path / "discord_permissions.yaml"
    path.write_text(
        "version: 1\n"
        "defaults:\n"
        "  command_prefix: \"!\"\n"
        "  require_prefix: false\n"
        "  allowed_guild_ids: [100]\n"
        "guilds:\n"
        "  - guild_id: 100\n"
        "    allowed_channel_ids: [200]\n"
        "    allowed_user_ids: [300]\n"
        f"    document_response_channel_ids: [{response_channel}]\n",
        encoding="utf-8",
    )
    return path


def test_registration_is_idempotent_and_content_free(tmp_path) -> None:
    repository = DurableJobRepository(str(tmp_path / "core.db"))
    enqueuer = DurableDocumentCompletionEnqueuer(repository)

    first = _register(enqueuer)
    second = _register(enqueuer)

    assert first["job_id"] == second["job_id"]
    jobs = repository.list_jobs(job_type=DOCUMENT_DISCORD_COMPLETION_JOB)
    assert len(jobs) == 1
    assert jobs[0]["aggregate_id"] == "doc-1"
    assert jobs[0]["payload"] == {
        "schema_version": 1,
        "document_id": "doc-1",
        "sink": "discord",
        "guild_id": "100",
        "channel_id": "200",
        "user_id": "300",
        "message_id": "400",
        "attachment_id": "500",
    }
    serialized = str(jobs[0]["payload"])
    assert "OCR finished" not in serialized
    assert "receipt.png" not in serialized
    repository.close()


def test_waiting_notification_defers_without_consuming_attempt_and_terminal_signal_wakes_it(
    tmp_path,
) -> None:
    repository = DurableJobRepository(str(tmp_path / "core.db"))
    _register(DurableDocumentCompletionEnqueuer(repository))
    documents = DocumentsStub(processing_state="processing")
    service = DocumentCompletionNotificationService(
        repository=repository,
        documents=documents,
        poll_delay_seconds=60,
        worker_id="notify-1",
    )

    job = service.claim()[0]
    assert service.prepare(job).disposition == "waiting"
    assert service.defer(job) is True
    deferred = repository.list_jobs(job_type=DOCUMENT_DISCORD_COMPLETION_JOB)[0]
    assert deferred["status"] == "retry"
    assert deferred["attempt_count"] == 0
    assert service.claim() == []

    released = DurableDocumentCompletionEnqueuer(repository).signal_terminal(
        document_id="doc-1",
        state="complete",
    )
    assert released == 1
    assert len(service.claim()) == 1
    repository.close()


def test_terminal_presentation_records_delivery_before_completion_and_survives_restart(
    tmp_path,
) -> None:
    repository = DurableJobRepository(str(tmp_path / "core.db"))
    _register(DurableDocumentCompletionEnqueuer(repository))
    documents = DocumentsStub(processing_state="complete", message="Here is the text: total $20")
    service = DocumentCompletionNotificationService(
        repository=repository,
        documents=documents,
        worker_id="notify-before-crash",
        lease_seconds=5,
    )

    job = service.claim()[0]
    prepared = service.prepare(job)
    assert prepared.disposition == "ready"
    assert prepared.message == "Here is the text: total $20"
    assert documents.contexts[0]["principal_kind"] == "discord_adapter"
    assert documents.contexts[0]["document_attachment_ids"] == ["doc-1"]
    assert service.record_delivery(job, message_id="900") is True

    restarted = DocumentCompletionNotificationService(
        repository=repository,
        documents=documents,
        worker_id="notify-after-crash",
        lease_seconds=5,
    )
    reclaimed = repository.claim_jobs(
        job_type=DOCUMENT_DISCORD_COMPLETION_JOB,
        worker_id=restarted.worker_id,
        limit=1,
        lease_seconds=5,
        now=datetime.now(UTC) + timedelta(seconds=6),
    )[0]
    assert restarted.prepare(reclaimed).disposition == "already_delivered"
    assert restarted.complete(reclaimed) is True
    assert repository.get_job(str(job["job_id"]))["status"] == "completed"
    repository.close()


def test_document_response_requires_separate_explicit_channel_policy(tmp_path) -> None:
    policy = load_discord_permissions_policy(str(_permissions(tmp_path, response_channel=200)))

    assert discord_document_response_allowed(policy, guild_id=100, channel_id=200) is True
    assert discord_document_response_allowed(policy, guild_id=100, channel_id=201) is False


def test_attachment_acceptance_registers_durable_completion_correlation(tmp_path) -> None:
    class AttachmentIngressStub:
        async def submit(self, descriptor):
            return DiscordAttachmentReceipt(
                filename=descriptor.filename,
                document_id="doc-1",
                intake_id="intake-1",
                state="queued",
                duplicate=False,
                enqueue_confirmed=True,
            )

    class NotificationsStub:
        def __init__(self) -> None:
            self.registrations = []

        def register_discord(self, **kwargs):
            self.registrations.append(kwargs)
            return {"job_id": "notification-1", "status": "pending"}

    notifications = NotificationsStub()
    permissions = _permissions(tmp_path)
    bot = DiscordJarvisBot(
        command_prefix="!",
        permissions_path=str(permissions),
        turn_service=SimpleNamespace(),
        attachment_ingress=AttachmentIngressStub(),
        document_completion_notifications=notifications,
        attachment_max_bytes=1024,
    )
    channel = SimpleNamespace(id=200, send=AsyncMock())
    message = SimpleNamespace(
        id=400,
        author=SimpleNamespace(bot=False, id=300, roles=[]),
        guild=SimpleNamespace(id=100),
        channel=channel,
        content="",
        attachments=[SimpleNamespace(
            id=500,
            filename="receipt.png",
            content_type="image/png",
            size=512,
            url="https://cdn.discordapp.com/attachments/200/500/receipt.png",
        )],
    )

    asyncio.run(bot.on_message(message))

    assert notifications.registrations == [{
        "document_id": "doc-1",
        "guild_id": "100",
        "channel_id": "200",
        "user_id": "300",
        "message_id": "400",
        "attachment_id": "500",
    }]


def test_core_bot_delivers_one_terminal_message_and_completes_job(tmp_path, monkeypatch) -> None:
    repository = DurableJobRepository(str(tmp_path / "core.db"))
    documents = DocumentsStub(processing_state="needs_review", message="OCR needs human review.")
    service = DocumentCompletionNotificationService(
        repository=repository,
        documents=documents,
        worker_id="notify-bot",
    )
    _register(service.enqueuer)
    permissions = _permissions(tmp_path)
    bot = DiscordJarvisBot(
        command_prefix="!",
        permissions_path=str(permissions),
        document_completion_notifications=service,
    )
    sent = SimpleNamespace(id=901)
    channel = SimpleNamespace(
        id=200,
        guild=SimpleNamespace(id=100),
        fetch_message=AsyncMock(return_value=SimpleNamespace(id=400)),
        send=AsyncMock(return_value=sent),
    )
    member = SimpleNamespace(id=300, roles=[])
    guild = SimpleNamespace(
        id=100,
        get_member=lambda user_id: member if user_id == 300 else None,
        fetch_member=AsyncMock(return_value=member),
    )
    monkeypatch.setattr(bot, "get_channel", lambda channel_id: channel if channel_id == 200 else None)
    monkeypatch.setattr(bot, "get_guild", lambda guild_id: guild if guild_id == 100 else None)

    assert asyncio.run(bot._run_document_notifications_once()) == 1

    channel.send.assert_awaited_once()
    assert channel.send.await_args.args[0] == "OCR needs human review."
    assert channel.send.await_args.kwargs["allowed_mentions"] is not None
    assert isinstance(channel.send.await_args.kwargs["nonce"], int)
    job = repository.list_jobs(job_type=DOCUMENT_DISCORD_COMPLETION_JOB)[0]
    assert job["status"] == "completed"
    assert job["provider_operation_ref"] == "discord:901"
    repository.close()


def test_core_bot_dead_letters_if_response_channel_permission_was_removed(
    tmp_path,
    monkeypatch,
) -> None:
    repository = DurableJobRepository(str(tmp_path / "core.db"))
    service = DocumentCompletionNotificationService(
        repository=repository,
        documents=DocumentsStub(processing_state="complete"),
        worker_id="notify-denied",
    )
    _register(service.enqueuer)
    permissions = _permissions(tmp_path, response_channel=201)
    bot = DiscordJarvisBot(
        command_prefix="!",
        permissions_path=str(permissions),
        document_completion_notifications=service,
    )
    channel = SimpleNamespace(id=200, send=AsyncMock())
    monkeypatch.setattr(bot, "get_channel", lambda _channel_id: channel)

    assert asyncio.run(bot._run_document_notifications_once()) == 0

    channel.send.assert_not_awaited()
    job = repository.list_jobs(job_type=DOCUMENT_DISCORD_COMPLETION_JOB)[0]
    assert job["status"] == "dead_letter"
    assert job["last_error_code"] == "discord_document_response_not_authorized"
    repository.close()
