from __future__ import annotations

from typing import Any

from app.db.sqlite_store import SQLiteStore


class ConversationSQLiteStorage:
    def __init__(self, sqlite_store: SQLiteStore) -> None:
        self._sqlite_store = sqlite_store

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
        return self._sqlite_store.upsert_conversation_topic(
            user_id=user_id,
            topic_key=topic_key,
            topic_label=topic_label,
            session_id=session_id,
            timestamp=timestamp,
            metadata=metadata,
        )

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
        return self._sqlite_store.insert_conversation_topic_history(
            timestamp=timestamp,
            topic_id=topic_id,
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            route=route,
            intent=intent,
            status=status,
            topic_key=topic_key,
            topic_label=topic_label,
            user_text=user_text,
            assistant_text=assistant_text,
            metadata=metadata,
        )

    def list_conversation_topics(
        self,
        *,
        user_id: str,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        return self._sqlite_store.list_conversation_topics(
            user_id=user_id,
            limit=limit,
        )

