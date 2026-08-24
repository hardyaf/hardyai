from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Protocol


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "get",
    "got",
    "have",
    "help",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "lets",
    "let",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "please",
    "that",
    "the",
    "this",
    "to",
    "we",
    "what",
    "when",
    "where",
    "who",
    "why",
    "with",
    "you",
    "your",
}


TOPIC_PATTERNS: list[tuple[str, str, str]] = [
    (r"\b(who are you|what is your name|what's your name|about yourself)\b", "identity", "Identity"),
    (r"\b(what can you do|capabilities|skills)\b", "capabilities", "Capabilities"),
    (r"\b(recipe|cook|meal|dinner|lunch|breakfast)\b", "cooking", "Cooking"),
    (r"\b(calendar|schedule|event|meeting|appointment)\b", "calendar", "Calendar"),
    (r"\b(list|grocery|groceries|to-do|todo|shopping)\b", "lists", "Lists"),
    (r"\b(light|lights|switch)\b", "lights", "Lights"),
    (r"\b(finance|budget|money|expense|report)\b", "finance", "Finance"),
    (r"\b(learn|explain|teach|understand)\b", "learning", "Learning"),
]


class ConversationHistoryPersistence(Protocol):
    def upsert_conversation_topic(
        self,
        *,
        user_id: str,
        topic_key: str,
        topic_label: str,
        session_id: str | None,
        timestamp: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create or update one topic aggregate row."""

    def insert_conversation_topic_history(
        self,
        *,
        timestamp: str,
        topic_id: str | None,
        session_id: str | None,
        user_id: str,
        agent_id: str,
        route: str,
        intent: str,
        status: str | None,
        topic_key: str,
        topic_label: str,
        user_text: str,
        assistant_text: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Append one per-turn conversation topic history row."""

    def list_conversation_topics(
        self,
        *,
        user_id: str,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Read aggregate conversation topics for one user."""


class ConversationHistoryService:
    def __init__(
        self,
        *,
        persistence: ConversationHistoryPersistence | None = None,
        base_dir: str | None = None,
    ) -> None:
        self._persistence = persistence
        root = Path(base_dir).expanduser() if base_dir else (Path.cwd() / "data" / "skill_history" / "conversation")
        self._base_dir = root.resolve()
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def record_turn(
        self,
        *,
        session_id: str | None,
        user_id: str,
        agent_id: str,
        route: str,
        intent: str,
        status: str | None,
        user_text: str,
        assistant_text: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        text = str(user_text or "").strip()
        if not text:
            return None
        timestamp = _utc_now()
        topic_key, topic_label, topic_terms = self._extract_topic(text)
        normalized_user = str(user_id or "").strip() or "local_user"
        normalized_agent = str(agent_id or "").strip().lower() or "jarvis"
        normalized_route = str(route or "").strip() or "main_jarvis"
        normalized_intent = str(intent or "").strip().lower() or "conversation.general"
        normalized_status = str(status or "").strip().lower() or None

        entry_metadata = dict(metadata or {})
        entry_metadata["topic_terms"] = topic_terms

        topic_id: str | None = None
        history_id: str | None = None
        if self._persistence is not None:
            topic_id = self._persistence.upsert_conversation_topic(
                user_id=normalized_user,
                topic_key=topic_key,
                topic_label=topic_label,
                session_id=session_id,
                timestamp=timestamp,
                metadata=entry_metadata,
            )
            history_id = self._persistence.insert_conversation_topic_history(
                timestamp=timestamp,
                topic_id=topic_id,
                session_id=session_id,
                user_id=normalized_user,
                agent_id=normalized_agent,
                route=normalized_route,
                intent=normalized_intent,
                status=normalized_status,
                topic_key=topic_key,
                topic_label=topic_label,
                user_text=text,
                assistant_text=str(assistant_text or "").strip() or None,
                metadata=entry_metadata,
            )

        entry = {
            "timestamp": timestamp,
            "history_id": history_id,
            "topic_id": topic_id,
            "session_id": session_id,
            "user_id": normalized_user,
            "agent_id": normalized_agent,
            "route": normalized_route,
            "intent": normalized_intent,
            "status": normalized_status,
            "topic_key": topic_key,
            "topic_label": topic_label,
            "topic_terms": topic_terms,
            "user_text": text,
            "assistant_text": str(assistant_text or "").strip() or None,
        }
        self._append_user_history(user_id=normalized_user, entry=entry)
        self._write_topics_snapshot(user_id=normalized_user)
        return entry

    @staticmethod
    def _extract_topic(text: str) -> tuple[str, str, list[str]]:
        lowered = text.strip().lower()
        for pattern, topic_key, topic_label in TOPIC_PATTERNS:
            if re.search(pattern, lowered):
                return topic_key, topic_label, [topic_key]

        tokens = re.findall(r"[a-z0-9']+", lowered)
        terms: list[str] = []
        for token in tokens:
            cleaned = token.strip("'")
            if not cleaned or len(cleaned) < 3:
                continue
            if cleaned in STOPWORDS:
                continue
            if cleaned not in terms:
                terms.append(cleaned)
            if len(terms) >= 4:
                break
        if not terms:
            return "general_conversation", "General Conversation", []
        key_terms = terms[:3]
        topic_key = "_".join(key_terms)
        topic_label = " ".join(key_terms).title()
        return topic_key, topic_label, terms

    @staticmethod
    def _safe_user_dir_name(user_id: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", user_id.strip())
        normalized = normalized.strip("._")
        return normalized or "local_user"

    def _append_user_history(self, *, user_id: str, entry: dict[str, Any]) -> None:
        user_dir = self._base_dir / self._safe_user_dir_name(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        history_path = user_dir / "history.jsonl"
        with self._lock:
            with history_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=True) + "\n")

    def _write_topics_snapshot(self, *, user_id: str) -> None:
        if self._persistence is None:
            return
        topics = self._persistence.list_conversation_topics(user_id=user_id, limit=500)
        user_dir = self._base_dir / self._safe_user_dir_name(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = user_dir / "topics_snapshot.json"
        payload = {
            "user_id": user_id,
            "topic_count": len(topics),
            "topics": topics,
        }
        with self._lock:
            snapshot_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
