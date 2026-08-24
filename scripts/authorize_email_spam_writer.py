from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import settings
from app.services.google.calendar_live import GoogleCalendarLiveService
from app.services.google.gmail_gateway import build_gmail_service, enable_native_google_tls_trust
from app.services.google.gmail_spam_writer import GMAIL_MODIFY_SCOPE
from app.skills.domains.email_agent.config import EmailAgentPermissions


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create the isolated Gmail modify token used only by Jarvis's manual mailbox worker. "
            "The conversational API does not load this token."
        )
    )
    parser.add_argument("--google-permissions-path", default=settings.google_permissions_path)
    parser.add_argument("--email-permissions-path", default=settings.email_agent_permissions_path)
    parser.add_argument("--token-path", default=settings.email_agent_spam_token_path)
    parser.add_argument("--force-consent", action="store_true")
    return parser


def _resolved(path_value: str) -> Path:
    path = Path(str(path_value or "").strip()).expanduser()
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    return path


def _write_token(path: Path, token_json: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(token_json)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main() -> int:
    args = _parser().parse_args()
    enable_native_google_tls_trust()
    token_path = _resolved(args.token_path)
    email_permissions = EmailAgentPermissions.load(args.email_permissions_path)
    calendar_live = GoogleCalendarLiveService(str(args.google_permissions_path))
    google_config = calendar_live._load_permissions()
    oauth_config = google_config.get("oauth") or {}
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError("Google OAuth dependencies are required.") from exc
    credentials = None
    if token_path.exists() and not args.force_consent:
        loaded = json.loads(token_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            credentials = Credentials.from_authorized_user_info(
                loaded,
                scopes=[GMAIL_MODIFY_SCOPE],
            )
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials or not credentials.valid:
        client_config = calendar_live._resolve_client_config(oauth_config)
        flow = InstalledAppFlow.from_client_config(
            client_config,
            scopes=[GMAIL_MODIFY_SCOPE],
        )
        flow.redirect_uri = str(
            oauth_config.get("redirect_uri") or "http://localhost:8080/oauth2/callback"
        )
        credentials = flow.run_local_server(
            port=0,
            access_type="offline",
            prompt="consent",
            include_granted_scopes="false",
        )
    gmail = build_gmail_service(credentials)
    profile = gmail.users().getProfile(userId="me").execute()
    actual_profile = str(profile.get("emailAddress") or "").strip().casefold()
    if actual_profile != email_permissions.gmail_profile:
        raise RuntimeError("Authorized spam-writer profile does not match the Jarvis Gmail mailbox.")
    if hasattr(credentials, "has_scopes") and not credentials.has_scopes([GMAIL_MODIFY_SCOPE]):
        raise RuntimeError("Google did not grant gmail.modify to the isolated spam-writer token.")
    token_data = json.loads(credentials.to_json())
    if not isinstance(token_data, dict):
        raise RuntimeError("OAuth completed without a serializable spam-writer token.")
    granted_scopes = {
        str(item).strip()
        for item in token_data.get("scopes") or []
        if str(item).strip()
    }
    if granted_scopes != {GMAIL_MODIFY_SCOPE}:
        raise RuntimeError(
            "Google returned broader scopes than the isolated worker requested; token was not saved."
        )
    _write_token(token_path, json.dumps(token_data, indent=2, sort_keys=True))
    if os.name != "nt" and stat.S_IMODE(token_path.stat().st_mode) & 0o077:
        raise RuntimeError("Spam-writer token permissions are broader than 0600.")
    print(
        json.dumps(
            {
                "status": "ok",
                "gmail_profile": actual_profile,
                "gmail_modify": True,
                "token_path": str(token_path),
                "api_process_loads_token": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
