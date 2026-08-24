from __future__ import annotations

import json
import re
from pathlib import Path

from app.memory.types import MemoryEntry


def _safe_slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or "entry"


class MarkdownMemoryStore:
    def __init__(self, base_dir: str) -> None:
        path = Path(base_dir).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        path.mkdir(parents=True, exist_ok=True)
        self._base_dir = path

    def add_entry(self, entry: MemoryEntry) -> None:
        day = entry.timestamp[:10] if len(entry.timestamp) >= 10 else "unknown-date"
        day_dir = self._base_dir / day
        day_dir.mkdir(parents=True, exist_ok=True)
        time_part = entry.timestamp.replace(":", "").replace(".", "").replace("+", "_")
        slug = _safe_slug(entry.intent)
        filename = day_dir / f"{time_part}_{slug}.md"
        content = (
            f"# Memory Entry\n\n"
            f"- timestamp: {entry.timestamp}\n"
            f"- session_id: {entry.session_id}\n"
            f"- user_id: {entry.user_id}\n"
            f"- source: {entry.source}\n"
            f"- intent: {entry.intent}\n"
            f"- route: {entry.route}\n\n"
            f"## Request\n\n"
            f"{entry.request_text}\n\n"
            f"## Response Summary\n\n"
            f"{entry.response_summary}\n\n"
            f"## Metadata\n\n"
            f"```json\n{json.dumps(entry.metadata, indent=2, ensure_ascii=True)}\n```\n"
        )
        filename.write_text(content, encoding="utf-8")

    def recent_entries(self, limit: int = 50) -> list[MemoryEntry]:
        # Markdown store is write-oriented for now; SQLite remains the read path.
        return []

