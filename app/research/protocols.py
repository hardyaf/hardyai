from __future__ import annotations

from typing import Any, Protocol

from app.research.types import ResearchDecision, SearchResult


class SearchProvider(Protocol):
    provider_name: str

    def search(
        self,
        *,
        query: str,
        limit: int,
        safe_search: int,
    ) -> list[SearchResult]:
        """Return normalized search snippets and canonical source URLs."""


class ResearchDecisionBackend(Protocol):
    def decide(
        self,
        *,
        text: str,
        context: dict[str, Any],
    ) -> ResearchDecision | None:
        """Choose direct answer, research, or clarification without answering."""
