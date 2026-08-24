from __future__ import annotations

import httpx

from app.core.main_jarvis import MainJarvis
from app.research.searxng import SearxngSearchProvider
from app.research.service import WebResearchService
from app.research.types import ResearchDecision, SearchResult


class _FakeProvider:
    provider_name = "fake-search"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def search(self, *, query: str, limit: int, safe_search: int):
        self.calls.append({"query": query, "limit": limit, "safe_search": safe_search})
        return [
            SearchResult(
                source_id=1,
                title="Current answer",
                url="https://example.test/current",
                snippet="The current answer is 42.",
                engine="test",
            )
        ]


class _DecisionBackend:
    def __init__(self, mode: str = "direct") -> None:
        self.mode = mode

    def decide(self, *, text: str, context: dict):
        return ResearchDecision(
            mode=self.mode,
            query=text if self.mode == "research" else None,
            confidence=0.9,
            reason="test_decision",
        )


class _ConversationBackend:
    def __init__(self) -> None:
        self.contexts: list[dict] = []

    def respond(self, text: str, context=None):
        self.contexts.append(dict(context or {}))
        return "The answer is 42 [1]. https://fabricated.invalid/source"


def test_main_conversation_researches_fresh_question_and_appends_canonical_sources():
    provider = _FakeProvider()
    service = WebResearchService(
        provider=provider,
        decision_backend=_DecisionBackend("direct"),
        enabled=True,
    )
    backend = _ConversationBackend()
    main = MainJarvis(conversation_backend=backend, research_service=service)

    response = main.respond(
        text="What is the current answer?",
        context={"micro_intent": "conversation.general"},
    )

    assert response["status"] == "conversation"
    assert response["conversation_source"] == "model_with_web_research"
    assert response["research"]["status"] == "ok"
    assert provider.calls[0]["safe_search"] == 1
    assert backend.contexts[0]["web_research"]["results"][0]["snippet"]
    assert "https://example.test/current" in response["message"]
    assert "https://fabricated.invalid/source" not in response["message"]


def test_research_decision_can_keep_stable_conversation_local():
    provider = _FakeProvider()
    service = WebResearchService(
        provider=provider,
        decision_backend=_DecisionBackend("direct"),
        enabled=True,
    )

    outcome = service.research_if_needed(
        text="Tell me a short story about a lion",
        context={},
    )

    assert outcome is None
    assert provider.calls == []


def test_child_research_is_disabled_by_default():
    provider = _FakeProvider()
    service = WebResearchService(
        provider=provider,
        decision_backend=_DecisionBackend("research"),
        enabled=True,
        children_enabled=False,
    )

    outcome = service.research_if_needed(
        text="What is current today?",
        context={"is_child": True},
    )

    assert outcome is None
    assert provider.calls == []


def test_searxng_provider_normalizes_and_filters_results():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["format"] == "json"
        assert request.url.params["safesearch"] == "2"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "<b>Useful</b> result",
                        "url": "https://example.test/page",
                        "content": "A <em>grounded</em> snippet.",
                        "engine": "example",
                    },
                    {"title": "duplicate", "url": "https://example.test/page", "content": "dup"},
                    {"title": "unsafe", "url": "javascript:alert(1)", "content": "bad"},
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = SearxngSearchProvider(base_url="http://searxng:8080", client=client)
    try:
        results = provider.search(query="test", limit=5, safe_search=2)
    finally:
        client.close()

    assert len(results) == 1
    assert results[0].title == "Useful result"
    assert results[0].snippet == "A grounded snippet."
