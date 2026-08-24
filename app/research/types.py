from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SearchResult:
    source_id: int
    title: str
    url: str
    snippet: str
    engine: str | None = None
    published_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchDecision:
    mode: str
    query: str | None
    confidence: float
    reason: str


@dataclass(frozen=True)
class ResearchOutcome:
    required: bool
    attempted: bool
    status: str
    query: str | None
    provider: str
    reason: str
    results: list[SearchResult] = field(default_factory=list)
    error_code: str | None = None

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "provider": self.provider,
            "results": [item.to_dict() for item in self.results],
        }

    def public_payload(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "attempted": self.attempted,
            "status": self.status,
            "query": self.query,
            "provider": self.provider,
            "reason": self.reason,
            "error_code": self.error_code,
            "sources": [
                {
                    "source_id": item.source_id,
                    "title": item.title,
                    "url": item.url,
                    "engine": item.engine,
                    "published_at": item.published_at,
                }
                for item in self.results
            ],
        }
