from __future__ import annotations

from app.config import settings
from app.integrations.discord_attachment.client import DiscordAttachmentIngressClient
from app.services.discord.bot import DiscordJarvisBot
from app.services.offline_runtime_policy import validate_offline_runtime


def main() -> int:
    validate_offline_runtime(settings, entrypoint="discord-adapter")
    if not settings.discord_enabled:
        print("DISCORD_ENABLED is false. Set DISCORD_ENABLED=true to run the bot.")
        return 1
    if not settings.discord_bot_token:
        print("DISCORD_BOT_TOKEN is missing.")
        return 1

    from app.runtime import turn_service

    attachment_ingress = None
    if settings.discord_attachment_ingress_enabled:
        attachment_ingress = DiscordAttachmentIngressClient(
            base_url=settings.discord_attachment_ingress_base_url,
            operator_key_path=settings.document_gateway_operator_key_path,
            timeout_seconds=settings.discord_attachment_ingress_timeout_seconds,
        )

    bot = DiscordJarvisBot(
        command_prefix=settings.discord_command_prefix,
        command_channel_id=settings.discord_command_channel_id,
        command_guild_id=settings.discord_command_guild_id,
        permissions_path=settings.discord_permissions_path,
        turn_service=turn_service,
        attachment_ingress=attachment_ingress,
        attachment_max_bytes=settings.documents_max_upload_bytes,
        attachment_max_per_message=settings.discord_attachment_max_per_message,
    )
    bot.run(settings.discord_bot_token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
