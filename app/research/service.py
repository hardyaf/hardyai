from __future__ import annotations

import re
from threading import RLock
from time import monotonic
from typing import Any

from app.research.protocols import ResearchDecisionBackend, SearchProvider
from app.research.types import ResearchDecision, ResearchOutcome, SearchResult


_EXPLICIT_RESEARCH_PATTERN = re.compile(
    r"\b(?:google|search(?: the)? web|look (?:it )?up|online|sources?|citations?|latest|current|today|"
    r"news|recent|right now|this week|price|release notes?)\b",
    flags=re.IGNORECASE,
)
_URL_PATTERN = re.compile(r"https?://[^\s)>\]}]+", flags=re.IGNORECASE)


class WebResearchService:
    def __init__(
        self,
        *,
        provider: SearchProvider,
        decision_backend: ResearchDecisionBackend | None = None,
        enabled: bool = False,
        max_results: int = 5,
        safe_search: int = 1,
        children_enabled: bool = False,
        cache_ttl_seconds: float = 900.0,
    ) -> None:
        self._provider = provider
        self._decision_backend = decision_backend
        self._enabled = bool(enabled)
        self._max_results = max(1, min(int(max_results), 8))
        self._safe_search = max(0, min(int(safe_search), 2))
        self._children_enabled = bool(children_enabled)
        self._cache_ttl_seconds = max(0.0, float(cache_ttl_seconds))
        self._cache: dict[tuple[str, int], tuple[float, list[SearchResult]]] = {}
        self._lock = RLock()
        self._attempt_count = 0
        self._success_count = 0
        self._last_error_code: str | None = None

    def research_if_needed(self, *, text: str, context: dict[str, Any]) -> ResearchOutcome | None:
        if not self._enabled:
            return None
        is_child = bool(context.get("is_child"))
        if is_child and not self._children_enabled:
            return None

        decision = self._decision(text=text, context=context)
        if decision is None or decision.mode == "direct":
            return None
        if decision.mode == "clarify":
            return ResearchOutcome(
                required=True,
                attempted=False,
                status="needs_clarification",
                query=None,
                provider=self._provider.provider_name,
                reason=decision.reason,
            )

        query = self._minimal_query(decision.query or text)
        if not query:
            return ResearchOutcome(
                required=True,
                attempted=False,
                status="needs_clarification",
                query=None,
                provider=self._provider.provider_name,
                reason="empty_research_query",
            )
        effective_safe_search = 2 if is_child else self._safe_search
        with self._lock:
            self._attempt_count += 1
        try:
            results = self._search_cached(query=query, safe_search=effective_safe_search)
        except Exception as exc:
            error_code = type(exc).__name__
            with self._lock:
                self._last_error_code = error_code
            return ResearchOutcome(
                required=True,
                attempted=True,
                status="unavailable",
                query=query,
                provider=self._provider.provider_name,
                reason=decision.reason,
                error_code=error_code,
            )
        if not results:
            return ResearchOutcome(
                required=True,
                attempted=True,
                status="no_results",
                query=query,
                provider=self._provider.provider_name,
                reason=decision.reason,
            )
        with self._lock:
            self._success_count += 1
            self._last_error_code = None
        return ResearchOutcome(
            required=True,
            attempted=True,
            status="ok",
            query=query,
            provider=self._provider.provider_name,
            reason=decision.reason,
            results=results,
        )

    def ground_answer(self, *, answer: str, outcome: ResearchOutcome) -> str:
        cleaned = str(answer or "").strip()
        allowed_urls = {item.url for item in outcome.results}
        cleaned = _URL_PATTERN.sub(
            lambda match: match.group(0) if match.group(0).rstrip(".,") in allowed_urls else "[unverified link removed]",
            cleaned,
        )
        sources = []
        for item in outcome.results:
            title = str(item.title or item.url).replace("[", "(").replace("]", ")")
            sources.append(f"- [{title}]({item.url})")
        if sources:
            cleaned = f"{cleaned}\n\nSources:\n" + "\n".join(sources)
        return cleaned

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self._enabled,
                "provider": self._provider.provider_name,
                "children_enabled": self._children_enabled,
                "max_results": self._max_results,
                "safe_search": self._safe_search,
                "attempt_count": self._attempt_count,
                "success_count": self._success_count,
                "last_error_code": self._last_error_code,
            }

    def _decision(self, *, text: str, context: dict[str, Any]) -> ResearchDecision | None:
        if _EXPLICIT_RESEARCH_PATTERN.search(str(text or "")):
            return ResearchDecision(
                mode="research",
                query=self._minimal_query(text),
                confidence=0.99,
                reason="explicit_or_freshness_trigger",
            )
        if self._decision_backend is None:
            return None
        return self._decision_backend.decide(text=text, context=context)

    def _search_cached(self, *, query: str, safe_search: int) -> list[SearchResult]:
        cache_key = (query.lower(), safe_search)
        now = monotonic()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and now - cached[0] <= self._cache_ttl_seconds:
                return list(cached[1])
        results = self._provider.search(
            query=query,
            limit=self._max_results,
            safe_search=safe_search,
        )
        with self._lock:
            self._cache[cache_key] = (now, list(results))
        return list(results)

    @staticmethod
    def _minimal_query(value: str) -> str:
        query = re.sub(r"\s+", " ", str(value or "")).strip()
        query = re.sub(
            r"^(?:please\s+)?(?:google|search(?: the)? web(?: for)?|look up)\s+",
            "",
            query,
            flags=re.IGNORECASE,
        )
        return query[:240].strip()
