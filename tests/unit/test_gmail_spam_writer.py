from __future__ import annotations

import pytest

from app.services.google.gmail_spam_writer import GoogleGmailSpamWriter


class Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class MessagesResource:
    def __init__(self, label_snapshots):
        self.label_snapshots = list(label_snapshots)
        self.get_calls = []
        self.modify_calls = []

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return Request({"id": kwargs["id"], "labelIds": self.label_snapshots.pop(0)})

    def modify(self, **kwargs):
        self.modify_calls.append(kwargs)
        return Request({"id": kwargs["id"], "labelIds": ["SPAM"]})


class LabelsResource:
    def __init__(self, labels=None):
        self.labels = list(labels or [])
        self.create_calls = []

    def list(self, **kwargs):
        return Request({"labels": list(self.labels)})

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        created = {"id": f"Label_{len(self.labels) + 1}", "name": kwargs["body"]["name"]}
        self.labels.append(created)
        return Request(created)


class UsersResource:
    def __init__(self, *, profile_email, messages, labels):
        self.profile_email = profile_email
        self.messages_resource = messages
        self.labels_resource = labels

    def getProfile(self, **kwargs):
        return Request({"emailAddress": self.profile_email})

    def messages(self):
        return self.messages_resource

    def labels(self):
        return self.labels_resource


class GmailService:
    def __init__(self, *, profile_email, label_snapshots, labels=None):
        self.messages_resource = MessagesResource(label_snapshots)
        self.labels_resource = LabelsResource(labels)
        self.users_resource = UsersResource(
            profile_email=profile_email,
            messages=self.messages_resource,
            labels=self.labels_resource,
        )

    def users(self):
        return self.users_resource


def test_spam_writer_exposes_only_fixed_verified_label_transition():
    gmail = GmailService(
        profile_email="jarvis.house@example.com",
        label_snapshots=[["INBOX", "UNREAD"], ["SPAM", "UNREAD"]],
    )
    writer = GoogleGmailSpamWriter(
        expected_profile_email="jarvis.house@example.com",
        gmail_service=gmail,
    )

    writer.verify_profile()
    result = writer.move_to_spam(message_id="abc123", operation_id="op-1")

    assert result.verified is True
    assert result.provider_modified is True
    assert gmail.messages_resource.modify_calls == [
        {
            "userId": "me",
            "id": "abc123",
            "body": {"addLabelIds": ["SPAM"], "removeLabelIds": ["INBOX"]},
        }
    ]
    assert all(call["format"] == "minimal" for call in gmail.messages_resource.get_calls)
    assert not hasattr(writer, "send")
    assert not hasattr(writer, "trash")


def test_spam_writer_is_idempotent_when_provider_state_is_already_correct():
    gmail = GmailService(
        profile_email="jarvis.house@example.com",
        label_snapshots=[["SPAM", "UNREAD"], ["SPAM", "UNREAD"]],
    )
    writer = GoogleGmailSpamWriter(
        expected_profile_email="jarvis.house@example.com",
        gmail_service=gmail,
    )

    result = writer.move_to_spam(message_id="abc123", operation_id="op-1")

    assert result.verified is True
    assert result.provider_modified is False
    assert gmail.messages_resource.modify_calls == []


def test_mailbox_writer_marks_only_the_exact_message_read_and_verifies():
    gmail = GmailService(
        profile_email="jarvis.house@example.com",
        label_snapshots=[["INBOX", "UNREAD"], ["INBOX"]],
    )
    writer = GoogleGmailSpamWriter(
        expected_profile_email="jarvis.house@example.com",
        gmail_service=gmail,
    )

    result = writer.mark_read_complete(message_id="abc123", operation_id="op-read-1")

    assert result.verified is True
    assert result.labels_after == ("INBOX",)
    assert gmail.messages_resource.modify_calls == [
        {
            "userId": "me",
            "id": "abc123",
            "body": {"addLabelIds": [], "removeLabelIds": ["UNREAD"]},
        }
    ]


def test_mailbox_writer_creates_and_applies_only_allowlisted_managed_category():
    gmail = GmailService(
        profile_email="jarvis.house@example.com",
        labels=[{"id": "Label_1", "name": "Jarvis/Bills"}],
        label_snapshots=[["INBOX", "Label_1"], ["INBOX", "Label_2"]],
    )
    writer = GoogleGmailSpamWriter(
        expected_profile_email="jarvis.house@example.com",
        gmail_service=gmail,
    )

    result = writer.apply_managed_category(
        message_id="abc123",
        operation_id="op-label-1",
        label_name="Jarvis/Community Sports",
        managed_label_names=("Jarvis/Bills", "Jarvis/Community Sports"),
    )

    assert result.verified is True
    assert result.gmail_label_id == "Label_2"
    assert gmail.labels_resource.create_calls[0]["body"]["name"] == "Jarvis/Community Sports"
    assert gmail.messages_resource.modify_calls == [
        {
            "userId": "me",
            "id": "abc123",
            "body": {"addLabelIds": ["Label_2"], "removeLabelIds": ["Label_1"]},
        }
    ]

    with pytest.raises(ValueError, match="allowlist"):
        writer.apply_managed_category(
            message_id="abc123",
            operation_id="op-label-2",
            label_name="Jarvis/Not Configured",
            managed_label_names=("Jarvis/Bills",),
        )


def test_spam_writer_rejects_wrong_profile_and_invalid_message_id():
    writer = GoogleGmailSpamWriter(
        expected_profile_email="jarvis.house@example.com",
        gmail_service=GmailService(
            profile_email="wrong@example.com",
            label_snapshots=[["INBOX"]],
        ),
    )

    with pytest.raises(RuntimeError, match="does not match"):
        writer.verify_profile()
    with pytest.raises(ValueError, match="message ID"):
        writer.move_to_spam(message_id="../escape", operation_id="op-1")
