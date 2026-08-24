from __future__ import annotations

import base64

from app.services.google.gmail_mime import GmailMimeParser


def encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def gmail_message() -> dict:
    return {
        "id": "m1",
        "threadId": "t1",
        "historyId": "11",
        "internalDate": "1786900000000",
        "snippet": "Preview",
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": "WORK Person <person@example.edu>"},
                {"name": "To", "value": "jarvis.house@example.com"},
                {"name": "Delivered-To", "value": "jarvis.house+work@example.com"},
                {"name": "Subject", "value": "Budget review"},
                {"name": "Message-ID", "value": "<fixture-1@example.test>"},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": encoded("Use the plain text body.")}},
                {
                    "mimeType": "text/html",
                    "body": {"data": encoded("<p>HTML fallback</p><script>steal()</script>")},
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "../unsafe.pdf",
                    "headers": [{"name": "Content-Disposition", "value": "attachment"}],
                    "body": {"attachmentId": "a1", "size": 500},
                },
            ],
        },
    }


def test_mime_parser_prefers_plain_text_and_keeps_attachment_metadata_only():
    loads: list[tuple[str, str]] = []
    parsed = GmailMimeParser().parse(
        gmail_message(),
        attachment_loader=lambda message_id, attachment_id: loads.append((message_id, attachment_id)) or b"secret",
    )

    assert parsed.body_text == "Use the plain text body."
    assert parsed.trusted_delivery_addresses == ("jarvis.house+work@example.com",)
    assert parsed.sender_email == "person@example.edu"
    assert parsed.attachment_metadata[0].filename == "unsafe.pdf"
    assert parsed.attachment_metadata[0].attachment_id == "a1"
    assert loads == []
    assert "Use the plain text body" not in str(parsed.metadata_record(source_route_key="work"))


def test_mime_parser_sanitizes_html_when_plain_text_is_absent():
    message = gmail_message()
    message["payload"]["parts"] = [message["payload"]["parts"][1]]

    parsed = GmailMimeParser().parse(message)

    assert "HTML fallback" in parsed.body_text
    assert "steal" not in parsed.body_text

