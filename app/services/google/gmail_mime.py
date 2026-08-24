from __future__ import annotations

import base64
import hashlib
import html
import json
import re
from dataclasses import dataclass
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses
from html.parser import HTMLParser
from pathlib import PurePath
from typing import Any, Callable


TRUSTED_DELIVERY_HEADERS = frozenset(
    {"delivered-to", "x-original-to", "envelope-to", "x-forwarded-to"}
)
RECIPIENT_HEADERS = frozenset({"to", "cc", "bcc", *TRUSTED_DELIVERY_HEADERS})


@dataclass(frozen=True)
class AttachmentMetadata:
    filename: str
    mime_type: str
    size: int
    attachment_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size": self.size,
            "attachment_id": self.attachment_id,
        }


@dataclass(frozen=True)
class ParsedGmailMessage:
    gmail_message_id: str
    gmail_thread_id: str
    gmail_history_id: str
    internal_date_ms: int
    rfc_message_id: str | None
    sender_name: str | None
    sender_email: str | None
    recipients: tuple[str, ...]
    trusted_delivery_addresses: tuple[str, ...]
    subject: str
    snippet: str
    body_text: str
    attachment_metadata: tuple[AttachmentMetadata, ...]
    gmail_label_ids: tuple[str, ...]
    canonical_body_hash: str
    list_id: str | None

    def metadata_record(self, *, source_route_key: str) -> dict[str, Any]:
        return {
            "gmail_message_id": self.gmail_message_id,
            "gmail_thread_id": self.gmail_thread_id,
            "rfc_message_id": self.rfc_message_id,
            "source_route_key": source_route_key,
            "gmail_history_id": self.gmail_history_id,
            "internal_date": self.internal_date_ms,
            "sender_name": self.sender_name,
            "sender_email": self.sender_email,
            "recipient_headers_json": json.dumps(list(self.recipients), sort_keys=True),
            "subject": self.subject,
            "snippet": self.snippet,
            "gmail_label_ids_json": json.dumps(list(self.gmail_label_ids), sort_keys=True),
            "attachment_metadata_json": json.dumps(
                [item.to_dict() for item in self.attachment_metadata],
                sort_keys=True,
            ),
            "canonical_body_hash": self.canonical_body_hash,
            "list_id": self.list_id,
        }


class _HTMLTextExtractor(HTMLParser):
    BLOCKED = frozenset({"script", "style", "noscript", "svg", "canvas"})
    BREAKS = frozenset({"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._blocked_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        lowered = tag.casefold()
        if lowered in self.BLOCKED:
            self._blocked_depth += 1
        elif lowered in self.BREAKS and not self._blocked_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in self.BLOCKED and self._blocked_depth:
            self._blocked_depth -= 1
        elif lowered in self.BREAKS and not self._blocked_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._blocked_depth:
            self.parts.append(data)


class GmailMimeParser:
    MAX_PARTS_HARD = 200
    MAX_BODY_BYTES_HARD = 2 * 1024 * 1024
    MAX_ATTACHMENTS_HARD = 50

    def __init__(
        self,
        *,
        max_parts: int = 100,
        max_body_bytes: int = 1024 * 1024,
        max_attachments: int = 20,
    ) -> None:
        self.max_parts = max(1, min(int(max_parts), self.MAX_PARTS_HARD))
        self.max_body_bytes = max(1024, min(int(max_body_bytes), self.MAX_BODY_BYTES_HARD))
        self.max_attachments = max(0, min(int(max_attachments), self.MAX_ATTACHMENTS_HARD))

    def parse(
        self,
        message: dict[str, Any],
        *,
        attachment_loader: Callable[[str, str], bytes] | None = None,
    ) -> ParsedGmailMessage:
        message_id = str(message.get("id") or "").strip()
        thread_id = str(message.get("threadId") or "").strip()
        if not message_id or not thread_id:
            raise ValueError("Gmail message and thread IDs are required.")
        payload = message.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("Gmail message has no MIME payload.")

        headers = self._headers(payload)
        sender = getaddresses(headers.get("from", []))
        sender_name = sender[0][0].strip() if sender and sender[0][0].strip() else None
        sender_email = sender[0][1].strip().casefold() if sender and sender[0][1].strip() else None
        recipients = tuple(
            email_address.casefold()
            for _, email_address in getaddresses(
                [value for key in RECIPIENT_HEADERS for value in headers.get(key, [])]
            )
            if email_address and "@" in email_address
        )
        trusted_delivery = tuple(
            email_address.casefold()
            for _, email_address in getaddresses(
                [value for key in TRUSTED_DELIVERY_HEADERS for value in headers.get(key, [])]
            )
            if email_address and "@" in email_address
        )

        plain_parts: list[str] = []
        html_parts: list[str] = []
        attachments: list[AttachmentMetadata] = []
        part_count = 0
        decoded_bytes = 0

        def decode_part(part: dict[str, Any], *, allow_attachment_fetch: bool) -> bytes:
            nonlocal decoded_bytes
            body = part.get("body") if isinstance(part.get("body"), dict) else {}
            raw = self._decode_base64url(str(body.get("data") or ""))
            attachment_id = str(body.get("attachmentId") or "").strip()
            if not raw and attachment_id and allow_attachment_fetch and attachment_loader is not None:
                raw = attachment_loader(message_id, attachment_id)
            remaining = max(0, self.max_body_bytes - decoded_bytes)
            bounded = bytes(raw[:remaining])
            decoded_bytes += len(bounded)
            return bounded

        def add_attachment(part: dict[str, Any]) -> None:
            if len(attachments) >= self.max_attachments:
                return
            body = part.get("body") if isinstance(part.get("body"), dict) else {}
            attachments.append(
                AttachmentMetadata(
                    filename=self._safe_filename(str(part.get("filename") or "attachment")),
                    mime_type=str(part.get("mimeType") or "application/octet-stream")[:255],
                    size=max(0, _optional_int(body.get("size")) or 0),
                    attachment_id=str(body.get("attachmentId") or "").strip() or None,
                )
            )

        def walk(part: dict[str, Any]) -> None:
            nonlocal part_count
            if part_count >= self.max_parts or decoded_bytes >= self.max_body_bytes:
                return
            part_count += 1
            mime_type = str(part.get("mimeType") or "application/octet-stream").casefold()
            filename = str(part.get("filename") or "").strip()
            disposition = self._part_disposition(part)
            is_attachment = bool(filename) or disposition == "attachment"

            if is_attachment:
                add_attachment(part)

            if mime_type == "text/plain" and not is_attachment:
                plain_parts.append(self._decode_text(decode_part(part, allow_attachment_fetch=True), part))
            elif mime_type == "text/html" and not is_attachment:
                html_parts.append(self._html_to_text(self._decode_text(decode_part(part, allow_attachment_fetch=True), part)))
            elif mime_type == "message/rfc822":
                nested = decode_part(part, allow_attachment_fetch=True)
                if nested:
                    nested_plain, nested_html, nested_attachments = self._parse_nested_email(nested)
                    plain_parts.extend(nested_plain)
                    html_parts.extend(nested_html)
                    for metadata in nested_attachments:
                        if len(attachments) < self.max_attachments:
                            attachments.append(metadata)

            for child in part.get("parts") or []:
                if isinstance(child, dict):
                    walk(child)

        walk(payload)
        body_text = self._normalize_text("\n\n".join(item for item in plain_parts if item.strip()))
        if not body_text:
            body_text = self._normalize_text("\n\n".join(item for item in html_parts if item.strip()))
        body_text = body_text[: self.max_body_bytes]
        subject = self._header_first(headers, "subject")[:998]
        snippet = self._normalize_text(str(message.get("snippet") or ""))[:1000]
        canonical = hashlib.sha256(
            (subject + "\n" + body_text).encode("utf-8", errors="replace")
        ).hexdigest()
        return ParsedGmailMessage(
            gmail_message_id=message_id,
            gmail_thread_id=thread_id,
            gmail_history_id=str(message.get("historyId") or "").strip(),
            internal_date_ms=max(0, _optional_int(message.get("internalDate")) or 0),
            rfc_message_id=self._header_first(headers, "message-id") or None,
            sender_name=sender_name,
            sender_email=sender_email,
            recipients=tuple(dict.fromkeys(recipients)),
            trusted_delivery_addresses=tuple(dict.fromkeys(trusted_delivery)),
            subject=subject or "(no subject)",
            snippet=snippet,
            body_text=body_text,
            attachment_metadata=tuple(attachments),
            gmail_label_ids=tuple(
                str(item).strip() for item in message.get("labelIds") or [] if str(item).strip()
            ),
            canonical_body_hash=canonical,
            list_id=self._header_first(headers, "list-id") or None,
        )

    @staticmethod
    def _headers(payload: dict[str, Any]) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for row in payload.get("headers") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip().casefold()
            if name:
                result.setdefault(name, []).append(str(row.get("value") or ""))
        return result

    @staticmethod
    def _header_first(headers: dict[str, list[str]], name: str) -> str:
        values = headers.get(name.casefold()) or []
        return str(values[0] or "").replace("\x00", "").strip() if values else ""

    @staticmethod
    def _part_disposition(part: dict[str, Any]) -> str:
        for row in part.get("headers") or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("name") or "").casefold() == "content-disposition":
                return str(row.get("value") or "").split(";", 1)[0].strip().casefold()
        return ""

    @staticmethod
    def _decode_base64url(value: str) -> bytes:
        data = str(value or "").encode("ascii", errors="ignore")
        data += b"=" * (-len(data) % 4)
        try:
            return base64.urlsafe_b64decode(data)
        except Exception:
            return b""

    @staticmethod
    def _decode_text(raw: bytes, part: dict[str, Any]) -> str:
        charset = "utf-8"
        for row in part.get("headers") or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("name") or "").casefold() != "content-type":
                continue
            match = re.search(r"charset\s*=\s*[\"']?([^;\"']+)", str(row.get("value") or ""), re.I)
            if match:
                charset = match.group(1).strip()
                break
        try:
            return raw.decode(charset, errors="replace")
        except LookupError:
            return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _html_to_text(value: str) -> str:
        parser = _HTMLTextExtractor()
        try:
            parser.feed(value)
            parser.close()
        except Exception:
            return GmailMimeParser._normalize_text(re.sub(r"<[^>]+>", " ", value))
        return GmailMimeParser._normalize_text(html.unescape("".join(parser.parts)))

    def _parse_nested_email(
        self,
        raw: bytes,
    ) -> tuple[list[str], list[str], list[AttachmentMetadata]]:
        try:
            message = BytesParser(policy=policy.default).parsebytes(raw[: self.max_body_bytes])
        except Exception:
            return [], [], []
        plain: list[str] = []
        html_parts: list[str] = []
        attachments: list[AttachmentMetadata] = []
        for index, part in enumerate(message.walk()):
            if index >= self.max_parts:
                break
            if part.is_multipart():
                continue
            filename = part.get_filename()
            disposition = str(part.get_content_disposition() or "")
            raw_payload = part.get_payload(decode=True) or b""
            if filename or disposition == "attachment":
                attachments.append(
                    AttachmentMetadata(
                        filename=self._safe_filename(filename or "attachment"),
                        mime_type=str(part.get_content_type() or "application/octet-stream")[:255],
                        size=len(raw_payload),
                        attachment_id=None,
                    )
                )
                continue
            text = self._email_part_text(part, raw_payload)
            if part.get_content_type().casefold() == "text/plain":
                plain.append(text)
            elif part.get_content_type().casefold() == "text/html":
                html_parts.append(self._html_to_text(text))
        return plain, html_parts, attachments[: self.max_attachments]

    @staticmethod
    def _email_part_text(part: Message, raw: bytes) -> str:
        charset = part.get_content_charset() or "utf-8"
        try:
            return raw.decode(charset, errors="replace")
        except LookupError:
            return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _normalize_text(value: str) -> str:
        cleaned = str(value or "").replace("\x00", "")
        cleaned = re.sub(r"\r\n?", "\n", cleaned)
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    @staticmethod
    def _safe_filename(value: str) -> str:
        candidate = PurePath(str(value or "attachment").replace("\\", "/")).name
        candidate = re.sub(r"[\x00-\x1f\x7f]+", "", candidate).strip(" .")
        return (candidate or "attachment")[:255]


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
