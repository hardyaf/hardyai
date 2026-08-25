from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field, field_validator


class DiscordAttachmentDescriptor(BaseModel):
    guild_id: str | None = Field(default=None, max_length=32, pattern=r"^[0-9]+$")
    channel_id: str = Field(min_length=1, max_length=32, pattern=r"^[0-9]+$")
    user_id: str = Field(min_length=1, max_length=32, pattern=r"^[0-9]+$")
    message_id: str = Field(min_length=1, max_length=32, pattern=r"^[0-9]+$")
    attachment_id: str = Field(min_length=1, max_length=32, pattern=r"^[0-9]+$")
    filename: str = Field(min_length=1, max_length=180)
    content_type: str = Field(default="", max_length=100)
    size_bytes: int = Field(ge=1, le=104857600)
    source_url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=200)

    @field_validator("content_type")
    @classmethod
    def _normalize_content_type(cls, value: str) -> str:
        return str(value or "").split(";", 1)[0].strip().casefold()


class DiscordAttachmentReceipt(BaseModel):
    filename: str
    document_id: str
    intake_id: str
    state: str
    duplicate: bool
    enqueue_confirmed: bool


class DiscordAttachmentIngressPort(Protocol):
    async def submit(self, descriptor: DiscordAttachmentDescriptor) -> DiscordAttachmentReceipt:
        """Submit one already-authorized Discord attachment descriptor."""

