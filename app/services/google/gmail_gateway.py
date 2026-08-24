from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


def enable_native_google_tls_trust() -> None:
    """Use the Windows certificate store without ever disabling TLS verification."""

    import os

    if os.name != "nt":
        return
    try:
        import truststore
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("truststore is required for Google TLS on Windows.") from exc
    truststore.inject_into_ssl()


class GmailHistoryExpiredError(RuntimeError):
    """The committed Gmail history cursor is no longer accepted by Gmail."""


@dataclass(frozen=True)
class GmailProfile:
    email_address: str
    history_id: str
    messages_total: int | None = None
    threads_total: int | None = None


@dataclass(frozen=True)
class GmailMessageRef:
    message_id: str
    thread_id: str | None


@dataclass(frozen=True)
class GmailHistoryPage:
    messages: tuple[GmailMessageRef, ...]
    history_id: str
    next_page_token: str | None


@dataclass(frozen=True)
class GmailMessagePage:
    messages: tuple[GmailMessageRef, ...]
    next_page_token: str | None


class GmailReadOnlyGateway(Protocol):
    def profile(self) -> GmailProfile: ...

    def current_history_id(self) -> str: ...

    def list_history(
        self,
        *,
        start_history_id: str,
        page_token: str | None,
        limit: int,
    ) -> GmailHistoryPage: ...

    def search_messages(
        self,
        *,
        query: str,
        page_token: str | None,
        limit: int,
    ) -> GmailMessagePage: ...

    def get_message(self, *, message_id: str, format: str = "full") -> dict[str, Any]: ...

    def get_thread(self, *, thread_id: str, format: str = "full") -> dict[str, Any]: ...

    def get_attachment_bytes(self, *, message_id: str, attachment_id: str) -> bytes: ...


def _status_code(exc: Exception) -> int | None:
    response = getattr(exc, "resp", None) or getattr(exc, "response", None)
    raw = getattr(response, "status", None) or getattr(response, "status_code", None)
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


class GoogleGmailReadOnlyGateway:
    """A deliberately narrow wrapper around Gmail read methods.

    The skill receives this interface, never the discovery client. No outbound,
    draft, delete, archive, read-state, settings, or label mutation methods are
    exposed here.
    """

    MAX_PAGE_SIZE = 100
    ALLOWED_FORMATS = frozenset({"minimal", "metadata", "full", "raw"})

    def __init__(
        self,
        *,
        expected_profile_email: str,
        gmail_service: Any | None = None,
        service_factory: Callable[[], Any] | None = None,
    ) -> None:
        expected = str(expected_profile_email or "").strip().casefold()
        if not expected or "@" not in expected:
            raise ValueError("A valid expected Gmail profile address is required.")
        if gmail_service is None and service_factory is None:
            raise ValueError("gmail_service or service_factory is required.")
        self._expected_profile_email = expected
        self._gmail_service = gmail_service
        self._service_factory = service_factory

    @classmethod
    def from_calendar_live(
        cls,
        *,
        calendar_live: Any,
        account_key: str,
        expected_profile_email: str,
    ) -> "GoogleGmailReadOnlyGateway":
        account = str(account_key or "").strip()
        if not account:
            raise ValueError("A Google OAuth account key is required.")

        def _build() -> Any:
            credentials = load_google_credentials(
                calendar_live=calendar_live,
                account_key=account,
                scopes=[GMAIL_READONLY_SCOPE],
                allow_interactive=False,
            )
            return build_gmail_service(credentials)

        return cls(
            expected_profile_email=expected_profile_email,
            service_factory=_build,
        )

    def _gmail(self) -> Any:
        if self._gmail_service is None:
            if self._service_factory is None:  # pragma: no cover - constructor invariant
                raise RuntimeError("Gmail service factory unavailable.")
            self._gmail_service = self._service_factory()
        return self._gmail_service

    def profile(self) -> GmailProfile:
        raw = self._gmail().users().getProfile(userId="me").execute()
        email_address = str(raw.get("emailAddress") or "").strip().casefold()
        if email_address != self._expected_profile_email:
            raise RuntimeError("Authorized Gmail profile does not match the configured mailbox.")
        history_id = str(raw.get("historyId") or "").strip()
        if not history_id:
            raise RuntimeError("Gmail profile did not return a history ID.")
        return GmailProfile(
            email_address=email_address,
            history_id=history_id,
            messages_total=_optional_int(raw.get("messagesTotal")),
            threads_total=_optional_int(raw.get("threadsTotal")),
        )

    def current_history_id(self) -> str:
        return self.profile().history_id

    def list_history(
        self,
        *,
        start_history_id: str,
        page_token: str | None,
        limit: int,
    ) -> GmailHistoryPage:
        cursor = str(start_history_id or "").strip()
        if not cursor:
            raise ValueError("start_history_id is required.")
        kwargs: dict[str, Any] = {
            "userId": "me",
            "startHistoryId": cursor,
            "historyTypes": ["messageAdded"],
            "maxResults": max(1, min(int(limit), self.MAX_PAGE_SIZE)),
        }
        token = str(page_token or "").strip()
        if token:
            kwargs["pageToken"] = token
        try:
            raw = self._gmail().users().history().list(**kwargs).execute()
        except Exception as exc:
            if _status_code(exc) == 404:
                raise GmailHistoryExpiredError("Gmail history cursor expired.") from exc
            raise

        refs: list[GmailMessageRef] = []
        seen: set[str] = set()
        for history in raw.get("history") or []:
            if not isinstance(history, dict):
                continue
            for added in history.get("messagesAdded") or []:
                message = added.get("message") if isinstance(added, dict) else None
                if not isinstance(message, dict):
                    continue
                message_id = str(message.get("id") or "").strip()
                if not message_id or message_id in seen:
                    continue
                seen.add(message_id)
                refs.append(
                    GmailMessageRef(
                        message_id=message_id,
                        thread_id=str(message.get("threadId") or "").strip() or None,
                    )
                )
        return GmailHistoryPage(
            messages=tuple(refs),
            history_id=str(raw.get("historyId") or cursor).strip() or cursor,
            next_page_token=str(raw.get("nextPageToken") or "").strip() or None,
        )

    def search_messages(
        self,
        *,
        query: str,
        page_token: str | None,
        limit: int,
    ) -> GmailMessagePage:
        trusted_query = str(query or "").strip()
        if not trusted_query:
            raise ValueError("A bounded Gmail query is required.")
        kwargs: dict[str, Any] = {
            "userId": "me",
            "q": trusted_query,
            "maxResults": max(1, min(int(limit), self.MAX_PAGE_SIZE)),
        }
        token = str(page_token or "").strip()
        if token:
            kwargs["pageToken"] = token
        raw = self._gmail().users().messages().list(**kwargs).execute()
        refs: list[GmailMessageRef] = []
        for row in raw.get("messages") or []:
            if not isinstance(row, dict):
                continue
            message_id = str(row.get("id") or "").strip()
            if not message_id:
                continue
            refs.append(
                GmailMessageRef(
                    message_id=message_id,
                    thread_id=str(row.get("threadId") or "").strip() or None,
                )
            )
        return GmailMessagePage(
            messages=tuple(refs),
            next_page_token=str(raw.get("nextPageToken") or "").strip() or None,
        )

    def get_message(self, *, message_id: str, format: str = "full") -> dict[str, Any]:
        message_key = str(message_id or "").strip()
        format_value = str(format or "full").strip().lower()
        if not message_key:
            raise ValueError("message_id is required.")
        if format_value not in self.ALLOWED_FORMATS:
            raise ValueError("Unsupported Gmail message format.")
        raw = (
            self._gmail()
            .users()
            .messages()
            .get(userId="me", id=message_key, format=format_value)
            .execute()
        )
        if not isinstance(raw, dict):
            raise RuntimeError("Gmail returned an invalid message payload.")
        return raw

    def get_thread(self, *, thread_id: str, format: str = "full") -> dict[str, Any]:
        thread_key = str(thread_id or "").strip()
        format_value = str(format or "full").strip().lower()
        if not thread_key:
            raise ValueError("thread_id is required.")
        if format_value not in self.ALLOWED_FORMATS:
            raise ValueError("Unsupported Gmail thread format.")
        raw = (
            self._gmail()
            .users()
            .threads()
            .get(userId="me", id=thread_key, format=format_value)
            .execute()
        )
        if not isinstance(raw, dict):
            raise RuntimeError("Gmail returned an invalid thread payload.")
        return raw

    def get_attachment_bytes(self, *, message_id: str, attachment_id: str) -> bytes:
        import base64

        message_key = str(message_id or "").strip()
        attachment_key = str(attachment_id or "").strip()
        if not message_key or not attachment_key:
            raise ValueError("message_id and attachment_id are required.")
        raw = (
            self._gmail()
            .users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_key, id=attachment_key)
            .execute()
        )
        encoded = str(raw.get("data") or "").strip()
        data = encoded.encode("ascii", errors="ignore")
        data += b"=" * (-len(data) % 4)
        try:
            return base64.urlsafe_b64decode(data)
        except Exception:
            return b""


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def load_google_credentials(
    *,
    calendar_live: Any,
    account_key: str,
    scopes: list[str],
    allow_interactive: bool,
) -> Any:
    """Reuse the protected Google OAuth store without downscoping its access token.

    Calendar and read-only Gmail intentionally share one house-account token store.
    Google refresh requests honor the scopes passed by the caller, so refreshing
    that shared credential with only ``gmail.readonly`` can mint a Gmail-only
    access token and temporarily break Calendar until the next full-scope refresh.
    Always refresh with the configured shared scope superset plus the caller's
    required scopes. The gateway remains capability-limited to Gmail read methods.
    """

    enable_native_google_tls_trust()

    config = calendar_live._load_permissions()
    oauth_config = config.get("oauth") or {}
    configured_scopes = [
        str(item).strip()
        for item in oauth_config.get("scopes") or []
        if str(item).strip()
    ]
    effective_scopes = list(
        dict.fromkeys(
            [
                *configured_scopes,
                *(str(item).strip() for item in scopes if str(item).strip()),
            ]
        )
    )
    token_store_raw = str(oauth_config.get("token_store_path") or "data/google_tokens.json")
    token_store_path = calendar_live._resolve_path(token_store_raw, prefer_existing=False)
    token_store = calendar_live._load_token_store(token_store_path)
    credentials, token_store, changed = calendar_live._load_or_authorize_credentials(
        oauth_cfg=oauth_config,
        account_key=str(account_key or "").strip(),
        scopes=effective_scopes,
        token_store=token_store,
        allow_interactive=bool(allow_interactive),
    )
    if changed:
        calendar_live._save_token_store(token_store_path, token_store)
    return credentials


def build_gmail_service(credentials: Any) -> Any:
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("google-api-python-client is required for Gmail ingestion.") from exc
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)
