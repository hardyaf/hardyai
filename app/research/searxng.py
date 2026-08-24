from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.research.types import SearchResult


class SearxngSearchProvider:
    provider_name = "searxng"

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 15.0,
        language: str = "en-US",
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = str(base_url or "").rstrip("/")
        self._timeout = max(1.0, float(timeout_seconds))
        self._language = str(language or "all").strip() or "all"
        self._client = client

    def search(self, *, query: str, limit: int, safe_search: int) -> list[SearchResult]:
        cleaned_query = re.sub(r"\s+", " ", str(query or "")).strip()[:240]
        if not cleaned_query or not self._base_url:
            return []
        request = self._client.get if self._client is not None else httpx.get
        response = request(
            f"{self._base_url}/search",
            params={
                "q": cleaned_query,
                "format": "json",
                "language": self._language,
                "safesearch": max(0, min(int(safe_search), 2)),
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        raw_results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(raw_results, list):
            return []

        normalized: list[SearchResult] = []
        seen_urls: set[str] = set()
        for raw in raw_results:
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or "").strip()
            if not self._is_public_web_url(url) or url in seen_urls:
                continue
            title = self._plain_text(raw.get("title"))[:240] or url
            snippet = self._plain_text(raw.get("content"))[:1200]
            engine = str(raw.get("engine") or "").strip() or None
            published_at = str(raw.get("publishedDate") or raw.get("published_at") or "").strip() or None
            seen_urls.add(url)
            normalized.append(
                SearchResult(
                    source_id=len(normalized) + 1,
                    title=title,
                    url=url,
                    snippet=snippet,
                    engine=engine,
                    published_at=published_at,
                )
            )
            if len(normalized) >= max(1, min(int(limit), 8)):
                break
        return normalized

    @staticmethod
    def _plain_text(value: Any) -> str:
        text = html.unescape(str(value or ""))
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _is_public_web_url(value: str) -> bool:
        try:
            parsed = urlparse(value)
        except ValueError:
            return False
        return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)
