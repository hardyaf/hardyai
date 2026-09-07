from __future__ import annotations

import ipaddress
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from app.research.types import ResearchOutcome, SearchResult
from app.skills.tool_contracts import (
    ToolArgumentCanonicalizationError,
    ToolCallEnvelope,
    thaw_json,
)


RESEARCH_TYPED_TOOLS = frozenset({"research.search_web"})


def describe_capability(
    *,
    services: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    service = services.get("web_research_service")
    if service is None:
        return {
            "configured": False,
            "authorized_here": False,
            "availability": "disabled",
            "access_note": "Web research is disabled in this runtime.",
        }
    status = service.status()
    configured = status.get("enabled") is True
    authorized = (
        configured
        and context.get("is_child") is not True
        and service.request_authorized(context=context)
    )
    return {
        "configured": configured,
        "authorized_here": authorized,
        "availability": "available" if authorized else "restricted",
        "access_note": (
            "Bounded read-only web research is available in this request context."
            if authorized
            else "Web research is not authorized in this request context."
        ),
    }


class ResearchToolHandler:
    """Typed projection over the existing read-only WebResearchService."""

    SKILL_ID = "skill.research.web"

    def __init__(self, *, research_service: Any) -> None:
        self._research_service = research_service

    def canonicalize_tool_arguments(
        self,
        *,
        tool_id: str,
        validated_arguments: Mapping[str, Any],
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        if str(tool_id or "").strip().casefold() not in RESEARCH_TYPED_TOOLS:
            raise ToolArgumentCanonicalizationError("research_tool_unsupported")
        if request_context.get("is_child") is True or not self._research_service.request_authorized(
            context=request_context
        ):
            raise ToolArgumentCanonicalizationError("research_policy_denied")
        arguments = dict(validated_arguments)
        query = re.sub(r"\s+", " ", str(arguments.get("query") or "")).strip()[:240]
        if not query:
            raise ToolArgumentCanonicalizationError("research_query_missing")
        return {
            "query": query,
            "limit": max(1, min(int(arguments.get("limit", 5)), 8)),
        }

    def execute_tool(
        self,
        *,
        envelope: ToolCallEnvelope,
        services: dict[str, Any],
    ) -> dict[str, Any]:
        del services
        if not isinstance(envelope, ToolCallEnvelope) or envelope.skill_id != self.SKILL_ID:
            return self._denied("research_tool_envelope_invalid")
        if envelope.tool_id not in RESEARCH_TYPED_TOOLS:
            return self._denied("research_tool_unsupported")
        arguments = thaw_json(envelope.arguments)
        context = {
            "principal_kind": envelope.principal_kind,
            "principal_subject": envelope.principal_subject,
            "requested_by_user_id": envelope.user_id,
            "agent_id": envelope.agent_id,
            "source": envelope.source_interface,
            "source_interface": envelope.source_interface,
            "request_id": envelope.root_request_id,
        }
        outcome = self._research_service.search(
            query=str(arguments.get("query") or ""),
            limit=int(arguments.get("limit", 5)),
            context=context,
        )
        return self._project_outcome(
            outcome=outcome,
            requested_limit=int(arguments.get("limit", 5)),
        )

    def _project_outcome(
        self,
        *,
        outcome: ResearchOutcome,
        requested_limit: int,
    ) -> dict[str, Any]:
        limit = max(1, min(int(requested_limit), 8))
        results: list[dict[str, Any]] = []
        remaining_chars = 6_000
        for value in outcome.results:
            item = self._project_result(value, source_id=len(results) + 1)
            if item is None:
                continue
            fixed_chars = sum(
                len(str(field_value))
                for field_name, field_value in item.items()
                if field_name != "snippet"
            )
            if fixed_chars > remaining_chars:
                break
            item["snippet"] = str(item["snippet"])[: min(1_200, remaining_chars - fixed_chars)]
            remaining_chars -= fixed_chars + len(item["snippet"])
            results.append(item)
            if len(results) >= limit:
                break
        payload = {
            "query": re.sub(r"\s+", " ", str(outcome.query or "")).strip()[:240],
            "results": results,
            "truncated": len(outcome.results) >= limit or len(results) < len(outcome.results),
            "safe_search": self._safe_search_level(outcome),
            "untrusted": True,
        }
        status = {
            "ok": "ok",
            "no_results": "ok",
            "needs_clarification": "needs_input",
            "unavailable": "retryable_error",
            "policy_denied": "policy_denied",
            "disabled": "policy_denied",
        }.get(str(outcome.status or "").strip().casefold(), "error")
        message = {
            "ok": "Returned bounded untrusted web search results.",
            "needs_input": "What should I search for?",
            "retryable_error": "The configured web research provider is temporarily unavailable.",
            "policy_denied": "Web research is not available in this request context.",
            "error": "Web research could not complete safely.",
        }[status]
        response: dict[str, Any] = {
            "status": status,
            "message": message,
            "payload": payload,
            "untrusted": True,
        }
        if status == "needs_input":
            response["missing_fields"] = ["query"]
        return response

    def _safe_search_level(self, outcome: ResearchOutcome) -> int:
        if outcome.safe_search is not None:
            return max(0, min(int(outcome.safe_search), 2))
        status = self._research_service.status()
        return max(0, min(int(status.get("safe_search") or 0), 2))

    @classmethod
    def _project_result(
        cls,
        value: SearchResult,
        *,
        source_id: int,
    ) -> dict[str, Any] | None:
        if not isinstance(value, SearchResult):
            return None
        url = cls._public_web_url(value.url)
        if not url:
            return None
        result: dict[str, Any] = {
            "source_id": max(1, min(int(source_id), 8)),
            "title": cls._text(value.title, 240) or url[:240],
            "url": url,
            "snippet": cls._text(value.snippet, 1_200),
        }
        engine = cls._text(value.engine, 80)
        if engine:
            result["engine"] = engine
        published_at = cls._text(value.published_at, 64)
        if published_at:
            result["published_at"] = published_at
        return result

    @staticmethod
    def _public_web_url(value: Any) -> str:
        candidate = str(value or "").strip()
        if not candidate or len(candidate) > 500 or any(ord(char) <= 32 for char in candidate):
            return ""
        try:
            parsed = urlsplit(candidate)
            port = parsed.port
        except ValueError:
            return ""
        del port
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return ""
        if parsed.username is not None or parsed.password is not None:
            return ""
        hostname = parsed.hostname.rstrip(".").casefold()
        if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
            return ""
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            return ""
        return candidate

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()[: max(0, int(limit))]

    @staticmethod
    def _denied(reason: str) -> dict[str, Any]:
        return {
            "status": "policy_denied",
            "message": "Web research is not available in this request context.",
            "denial_reason": reason,
        }


def run(
    *,
    intent: str,
    entities: dict[str, Any],
    services: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    service = services.get("web_research_service")
    if service is None:
        return {"status": "disabled", "message": "Web research is disabled."}
    if str(intent or "").strip().casefold() != "research.search_web":
        return {"status": "denied", "message": "Unsupported web research operation."}
    if context.get("is_child") is True or not service.request_authorized(context=context):
        return {
            "status": "denied",
            "message": "Web research is not available in this request context.",
            "_persistence_policy": "no_store",
        }
    outcome = service.search(
        query=str(entities.get("query") or ""),
        limit=int(entities.get("limit") or 5),
        context=context,
    )
    result = ResearchToolHandler(research_service=service)._project_outcome(
        outcome=outcome,
        requested_limit=int(entities.get("limit") or 5),
    )
    result["_persistence_policy"] = "no_store"
    return result
