from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import settings
from app.services.google.calendar_inbox import GMAIL_READONLY_SCOPE
from app.services.google.gmail_gateway import enable_native_google_tls_trust
from app.services.google.calendar_live import GoogleCalendarLiveService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Authorize the configured house Google account for Calendar Events and Gmail Readonly, "
            "then verify both APIs. Run this on a computer with an interactive browser."
        )
    )
    parser.add_argument(
        "--permissions-path",
        default=settings.google_permissions_path,
        help="Path to the protected Google permissions YAML.",
    )
    parser.add_argument(
        "--force-consent",
        action="store_true",
        help="Ignore the cached house token and request a fresh consent grant.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    enable_native_google_tls_trust()
    calendar_live = GoogleCalendarLiveService(str(args.permissions_path))
    config = calendar_live._load_permissions()
    calendar_config = config.get("calendar") or {}
    oauth_config = config.get("oauth") or {}
    bindings = calendar_live._calendar_bindings(calendar_config)
    host = calendar_live._select_host_binding(bindings, calendar_config)
    if host is None:
        raise RuntimeError("No house/default Google Calendar binding is configured.")

    account_key = calendar_live._resolve_account_key(host, config)
    scopes = calendar_live._oauth_scopes(oauth_cfg=oauth_config, include_write=True)
    if GMAIL_READONLY_SCOPE not in scopes:
        scopes.append(GMAIL_READONLY_SCOPE)
    token_store_raw = str(oauth_config.get("token_store_path") or "data/google_tokens.json")
    token_store_path = calendar_live._resolve_path(token_store_raw, prefer_existing=False)
    token_store = calendar_live._load_token_store(token_store_path)
    working_store = dict(token_store)
    cached = working_store.get(account_key)
    cached_scopes = {
        str(item).strip()
        for item in ((cached or {}).get("scopes") or [])
        if str(item).strip()
    } if isinstance(cached, dict) else set()
    if args.force_consent or GMAIL_READONLY_SCOPE not in cached_scopes:
        working_store.pop(account_key, None)

    credentials, working_store, _ = calendar_live._load_or_authorize_credentials(
        oauth_cfg=oauth_config,
        account_key=account_key,
        scopes=scopes,
        token_store=working_store,
        allow_interactive=True,
    )

    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("google-api-python-client is required.") from exc

    gmail = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    profile = gmail.users().getProfile(userId="me").execute()
    calendar = calendar_live._build_calendar_service(credentials)
    calendar.calendarList().get(calendarId=host.calendar_id).execute()

    # Save only after both APIs accept the new grant; a failed consent or API check preserves the old token.
    calendar_live._save_token_store(token_store_path, working_store)
    print(
        json.dumps(
            {
                "status": "ok",
                "account_key": account_key,
                "gmail_profile": str(profile.get("emailAddress") or ""),
                "house_calendar_id": host.calendar_id,
                "gmail_readonly": True,
                "calendar_events": True,
                "token_store_path": str(token_store_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
