from __future__ import annotations

import argparse
from copy import deepcopy
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if __package__:
    from scripts.configure_web_research import upsert_env_text
else:
    from configure_web_research import upsert_env_text

from app.skills.domains.email_agent.config import EmailAgentPermissions


def build_permissions(
    *,
    template: dict[str, Any],
    google_account_key: str,
) -> dict[str, Any]:
    raw = deepcopy(template)
    raw["google_account_key"] = str(google_account_key or "").strip()
    EmailAgentPermissions.from_mapping(raw)
    return raw


def load_permissions_template(path: Path) -> dict[str, Any]:
    source = _regular_file(path)
    loaded = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("email permissions template must be a YAML mapping")
    return loaded


def configure(
    *,
    env_path: Path,
    permissions_path: Path,
    permissions_template_path: Path,
    google_account_key: str,
    enable_sync: bool,
    enable_label_writes: bool = False,
    enable_spam_writes: bool = False,
    spam_token_path: str = "secrets/email-spam-worker/token.json",
    force: bool = False,
) -> None:
    permissions = build_permissions(
        template=load_permissions_template(permissions_template_path),
        google_account_key=google_account_key,
    )
    env_file = _regular_file(env_path)
    permissions_target = permissions_path.expanduser()
    if permissions_target.exists() and not force:
        raise ValueError(f"refusing to overwrite existing permissions file: {permissions_target}")
    permissions_target.parent.mkdir(parents=True, exist_ok=True)

    permissions_text = yaml.safe_dump(permissions, sort_keys=False, allow_unicode=False)
    _atomic_write(permissions_target, permissions_text, mode=0o600)
    original = env_file.read_text(encoding="utf-8")
    updated = upsert_env_text(
        original,
        {
            "EMAIL_AGENT_ENABLED": "true",
            "EMAIL_AGENT_SYNC_ENABLED": str(bool(enable_sync)).lower(),
            "EMAIL_AGENT_PERMISSIONS_PATH": str(permissions_target),
            "EMAIL_AGENT_LABEL_SHADOW_ENABLED": "true",
            "EMAIL_AGENT_LABEL_WRITES_ENABLED": str(bool(enable_label_writes)).lower(),
            "EMAIL_AGENT_LABEL_TOKEN_PATH": str(spam_token_path or "").strip(),
            "EMAIL_AGENT_SPAM_WRITES_ENABLED": str(bool(enable_spam_writes)).lower(),
            "EMAIL_AGENT_SPAM_TOKEN_PATH": str(spam_token_path or "").strip(),
            "EMAIL_AGENT_SPAM_WORKER_POLL_SECONDS": "2",
            "EMAIL_AGENT_SPAM_WORKER_BATCH_SIZE": "5",
            "EMAIL_AGENT_SPAM_WORKER_LEASE_SECONDS": "60",
            "EMAIL_AGENT_SPAM_MAX_ATTEMPTS": "3",
            "EMAIL_AGENT_SPAM_MAX_WRITES_PER_HOUR": "5",
            "EMAIL_AGENT_SPAM_MAX_WRITES_PER_DAY": "10",
            "EMAIL_AGENT_ALLOW_HISTORICAL_BACKFILL": "false",
            "EMAIL_AGENT_ALLOW_REMOTE_MODEL": "false",
        },
    )
    _atomic_write(env_file, updated, mode=stat.S_IMODE(env_file.stat().st_mode))


def configure_discord_access(
    *,
    permissions_path: Path,
    guild_id: str,
    channel_id: str,
    external_user_id: str,
) -> None:
    """Add the exact email-skill Discord scope without broadening global access."""

    guild_key = str(guild_id or "").strip()
    channel_key = str(channel_id or "").strip()
    user_key = str(external_user_id or "").strip()
    if not guild_key.isdigit() or not channel_key.isdigit() or not user_key.isdigit():
        raise ValueError("Discord guild, channel, and user IDs must be numeric.")
    target = _regular_file(permissions_path)
    loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or int(loaded.get("version") or 0) != 1:
        raise ValueError("Discord permissions YAML version must be 1.")
    guilds = loaded.get("guilds")
    if not isinstance(guilds, list):
        raise ValueError("Discord permissions YAML must define guilds as a list.")
    matches = [
        row
        for row in guilds
        if isinstance(row, dict) and str(row.get("guild_id") or "").strip() == guild_key
    ]
    if len(matches) != 1:
        raise ValueError("Exactly one matching Discord guild policy is required.")
    guild = matches[0]
    rows = guild.setdefault("skill_channel_access", [])
    if not isinstance(rows, list):
        raise ValueError("skill_channel_access must be a list.")
    matching_rows = [
        row
        for row in rows
        if isinstance(row, dict)
        and str(row.get("skill_id") or "").strip().casefold() == "skill.email.agent"
        and str(row.get("channel_id") or "").strip() == channel_key
    ]
    if len(matching_rows) > 1:
        raise ValueError("Duplicate email-agent Discord channel scopes must be resolved first.")
    if matching_rows:
        row = matching_rows[0]
        users = {
            str(item).strip()
            for item in row.get("allowed_user_ids") or []
            if str(item).strip().isdigit()
        }
        users.add(user_key)
        row["allowed_user_ids"] = sorted(users, key=int)
        row["audiences"] = ["shared"]
    else:
        rows.append(
            {
                "skill_id": "skill.email.agent",
                "channel_id": channel_key,
                "allowed_user_ids": [user_key],
                "audiences": ["shared"],
            }
        )
    _atomic_write(
        target,
        yaml.safe_dump(loaded, sort_keys=False, allow_unicode=False),
        mode=stat.S_IMODE(target.stat().st_mode),
    )


def _regular_file(path: Path) -> Path:
    candidate = path.expanduser()
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError(f"refusing to edit non-regular environment file: {candidate}")
    return candidate.resolve()


def _atomic_write(path: Path, text: str, *, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure the shared Jarvis email agent.")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--permissions-file",
        default="secrets/live/email_agent_permissions.yaml",
    )
    parser.add_argument("--permissions-template", required=True)
    parser.add_argument("--google-account-key", required=True)
    parser.add_argument("--discord-permissions-file")
    parser.add_argument("--discord-guild-id")
    parser.add_argument("--discord-channel-id")
    parser.add_argument("--discord-external-user-id")
    parser.add_argument("--enable-sync", action="store_true")
    parser.add_argument("--enable-label-writes", action="store_true")
    parser.add_argument("--enable-spam-writes", action="store_true")
    parser.add_argument(
        "--spam-token-file",
        default="secrets/email-spam-worker/token.json",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    configure(
        env_path=Path(args.env_file),
        permissions_path=Path(args.permissions_file),
        permissions_template_path=Path(args.permissions_template),
        google_account_key=args.google_account_key,
        enable_sync=args.enable_sync,
        enable_label_writes=args.enable_label_writes,
        enable_spam_writes=args.enable_spam_writes,
        spam_token_path=args.spam_token_file,
        force=args.force,
    )
    if any(
        (
            args.discord_permissions_file,
            args.discord_guild_id,
            args.discord_channel_id,
            args.discord_external_user_id,
        )
    ):
        if not all(
            (
                args.discord_permissions_file,
                args.discord_guild_id,
                args.discord_channel_id,
                args.discord_external_user_id,
            )
        ):
            parser.error(
                "Discord access requires --discord-permissions-file, --discord-guild-id, "
                "--discord-channel-id, and --discord-external-user-id"
            )
        configure_discord_access(
            permissions_path=Path(args.discord_permissions_file),
            guild_id=args.discord_guild_id,
            channel_id=args.discord_channel_id,
            external_user_id=args.discord_external_user_id,
        )
    print(
        "Email agent configuration written with shared categories, managed label writes "
        f"{'enabled' if args.enable_label_writes else 'disabled'}, "
        f"sync {'enabled' if args.enable_sync else 'disabled'}, and manual mailbox writes "
        f"{'enabled' if args.enable_spam_writes else 'disabled'}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
