from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from app.services.google.gmail_gateway import build_gmail_service, enable_native_google_tls_trust


GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
_MESSAGE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_MANAGED_LABEL_NAME = re.compile(r"Jarvis/[^\x00-\x1f\x7f]{1,218}", re.IGNORECASE)


def _replace_token(path: Path, token_json: str) -> None:
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


@dataclass(frozen=True)
class GmailSpamWriteResult:
    message_id: str
    labels_before: tuple[str, ...]
    labels_after: tuple[str, ...]
    provider_modified: bool
    verified: bool
    gmail_label_id: str | None = None


class GmailSpamWriter(Protocol):
    def verify_profile(self) -> None: ...

    def move_to_spam(
        self,
        *,
        message_id: str,
        operation_id: str,
    ) -> GmailSpamWriteResult: ...

    def mark_read_complete(
        self,
        *,
        message_id: str,
        operation_id: str,
    ) -> GmailSpamWriteResult: ...

    def apply_managed_category(
        self,
        *,
        message_id: str,
        operation_id: str,
        label_name: str,
        managed_label_names: tuple[str, ...],
    ) -> GmailSpamWriteResult: ...


class GoogleGmailSpamWriter:
    """Method-confined Gmail writer for fixed, reversible label transitions.

    The OAuth scope is broader than this interface. Keep this class and its token
    in the isolated worker process; never pass the discovery client to a skill.
    """

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
    def from_token_file(
        cls,
        *,
        expected_profile_email: str,
        token_path: str,
    ) -> "GoogleGmailSpamWriter":
        path = Path(str(token_path or "").strip()).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()

        def _build() -> Any:
            enable_native_google_tls_trust()
            try:
                from google.auth.transport.requests import Request
                from google.oauth2.credentials import Credentials
            except ImportError as exc:  # pragma: no cover - dependency guard
                raise RuntimeError("Google OAuth dependencies are required for the spam worker.") from exc
            if not path.exists() or not path.is_file():
                raise RuntimeError("The isolated Gmail spam-writer token is not configured.")
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise RuntimeError("The Gmail spam-writer token file is invalid.")
            stored_scopes = {
                str(item).strip()
                for item in loaded.get("scopes") or []
                if str(item).strip()
            }
            if stored_scopes != {GMAIL_MODIFY_SCOPE}:
                raise RuntimeError("The Gmail spam-writer token does not contain exactly gmail.modify.")
            credentials = Credentials.from_authorized_user_info(
                loaded,
                scopes=[GMAIL_MODIFY_SCOPE],
            )
            if hasattr(credentials, "has_scopes") and not credentials.has_scopes([GMAIL_MODIFY_SCOPE]):
                raise RuntimeError("The Gmail spam-writer token lacks gmail.modify.")
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
                _replace_token(path, credentials.to_json())
            if not credentials.valid:
                raise RuntimeError("The Gmail spam-writer token is not valid.")
            return build_gmail_service(credentials)

        return cls(
            expected_profile_email=expected_profile_email,
            service_factory=_build,
        )

    def _gmail(self) -> Any:
        if self._gmail_service is None:
            if self._service_factory is None:  # pragma: no cover - constructor invariant
                raise RuntimeError("Gmail spam-writer service factory unavailable.")
            self._gmail_service = self._service_factory()
        return self._gmail_service

    def verify_profile(self) -> None:
        raw = self._gmail().users().getProfile(userId="me").execute()
        actual = str(raw.get("emailAddress") or "").strip().casefold()
        if actual != self._expected_profile_email:
            raise RuntimeError("Authorized spam-writer profile does not match the configured mailbox.")

    def _label_ids(self, *, message_id: str) -> frozenset[str]:
        key = self._message_id(message_id)
        raw = (
            self._gmail()
            .users()
            .messages()
            .get(userId="me", id=key, format="minimal")
            .execute()
        )
        if not isinstance(raw, dict):
            raise RuntimeError("Gmail returned an invalid message payload during spam verification.")
        return frozenset(str(item) for item in raw.get("labelIds") or [] if str(item).strip())

    def move_to_spam(
        self,
        *,
        message_id: str,
        operation_id: str,
    ) -> GmailSpamWriteResult:
        key = self._message_id(message_id)
        operation_key = str(operation_id or "").strip()
        if not operation_key:
            raise ValueError("operation_id is required.")
        before = self._label_ids(message_id=key)
        already_applied = "SPAM" in before and "INBOX" not in before
        if not already_applied:
            (
                self._gmail()
                .users()
                .messages()
                .modify(
                    userId="me",
                    id=key,
                    body={"addLabelIds": ["SPAM"], "removeLabelIds": ["INBOX"]},
                )
                .execute()
            )
        after = self._label_ids(message_id=key)
        verified = "SPAM" in after and "INBOX" not in after
        return GmailSpamWriteResult(
            message_id=key,
            labels_before=tuple(sorted(before)),
            labels_after=tuple(sorted(after)),
            provider_modified=not already_applied,
            verified=verified,
        )

    def mark_read_complete(
        self,
        *,
        message_id: str,
        operation_id: str,
    ) -> GmailSpamWriteResult:
        key = self._message_id(message_id)
        operation_key = str(operation_id or "").strip()
        if not operation_key:
            raise ValueError("operation_id is required.")
        before = self._label_ids(message_id=key)
        already_applied = "UNREAD" not in before
        if not already_applied:
            (
                self._gmail()
                .users()
                .messages()
                .modify(
                    userId="me",
                    id=key,
                    body={"addLabelIds": [], "removeLabelIds": ["UNREAD"]},
                )
                .execute()
            )
        after = self._label_ids(message_id=key)
        return GmailSpamWriteResult(
            message_id=key,
            labels_before=tuple(sorted(before)),
            labels_after=tuple(sorted(after)),
            provider_modified=not already_applied,
            verified="UNREAD" not in after,
        )

    def apply_managed_category(
        self,
        *,
        message_id: str,
        operation_id: str,
        label_name: str,
        managed_label_names: tuple[str, ...],
    ) -> GmailSpamWriteResult:
        key = self._message_id(message_id)
        if not str(operation_id or "").strip():
            raise ValueError("operation_id is required.")
        target_name = self._managed_label_name(label_name)
        allowed_names = tuple(
            dict.fromkeys(self._managed_label_name(item) for item in managed_label_names)
        )
        if target_name not in allowed_names:
            raise ValueError("The target Gmail label is not in the managed allowlist.")

        labels_by_name = self._labels_by_name()
        target_id = labels_by_name.get(target_name)
        if target_id is None:
            created = (
                self._gmail()
                .users()
                .labels()
                .create(
                    userId="me",
                    body={
                        "name": target_name,
                        "labelListVisibility": "labelShow",
                        "messageListVisibility": "show",
                    },
                )
                .execute()
            )
            target_id = str(created.get("id") or "").strip() if isinstance(created, dict) else ""
            if not target_id:
                raise RuntimeError("Gmail did not return an ID for the managed label.")
            labels_by_name[target_name] = target_id

        managed_ids = {
            label_id
            for name, label_id in labels_by_name.items()
            if name in allowed_names and label_id
        }
        before = self._label_ids(message_id=key)
        remove_ids = sorted((managed_ids - {target_id}) & before)
        add_ids = [] if target_id in before else [target_id]
        provider_modified = bool(add_ids or remove_ids)
        if provider_modified:
            (
                self._gmail()
                .users()
                .messages()
                .modify(
                    userId="me",
                    id=key,
                    body={"addLabelIds": add_ids, "removeLabelIds": remove_ids},
                )
                .execute()
            )
        after = self._label_ids(message_id=key)
        other_managed_ids = managed_ids - {target_id}
        verified = target_id in after and not bool(other_managed_ids & after)
        return GmailSpamWriteResult(
            message_id=key,
            labels_before=tuple(sorted(before)),
            labels_after=tuple(sorted(after)),
            provider_modified=provider_modified,
            verified=verified,
            gmail_label_id=target_id,
        )

    def _labels_by_name(self) -> dict[str, str]:
        raw = self._gmail().users().labels().list(userId="me").execute()
        if not isinstance(raw, dict):
            raise RuntimeError("Gmail returned an invalid label catalog.")
        result: dict[str, str] = {}
        for item in raw.get("labels") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            label_id = str(item.get("id") or "").strip()
            if name and label_id:
                result[name] = label_id
        return result

    @staticmethod
    def _managed_label_name(value: str) -> str:
        name = str(value or "").strip()
        if not _MANAGED_LABEL_NAME.fullmatch(name):
            raise ValueError("Managed Gmail labels must use the Jarvis/ namespace.")
        return name

    @staticmethod
    def _message_id(value: str) -> str:
        key = str(value or "").strip()
        if not _MESSAGE_ID.fullmatch(key):
            raise ValueError("Invalid Gmail message ID.")
        return key
