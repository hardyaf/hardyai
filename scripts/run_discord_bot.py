from __future__ import annotations

import sys

from app.config import settings
from app.services.discord.bot import DiscordJarvisBot
from app.runtime import turn_service


def main() -> int:
    if not settings.discord_enabled:
        print("DISCORD_ENABLED is false. Set DISCORD_ENABLED=true to run the bot.")
        return 1
    if not settings.discord_bot_token:
        print("DISCORD_BOT_TOKEN is missing.")
        return 1

    bot = DiscordJarvisBot(
        command_prefix=settings.discord_command_prefix,
        command_channel_id=settings.discord_command_channel_id,
        command_guild_id=settings.discord_command_guild_id,
        permissions_path=settings.discord_permissions_path,
        turn_service=turn_service,
    )
    bot.run(settings.discord_bot_token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
