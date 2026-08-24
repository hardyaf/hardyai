from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.core.ollama_observability import OllamaCallObserver, OllamaMetricsCallback
from app.research.types import ResearchDecision


class OllamaResearchDecisionBackend:
    """Small structured reasoning pass that decides whether web evidence is needed."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        keep_alive_seconds: float | None = None,
        num_ctx: int = 12288,
        num_predict: int = 256,
        metrics_callback: OllamaMetricsCallback | None = None,
    ) -> None:
        self._base_url = str(base_url or "").rstrip("/")
        self._model = str(model or "").strip()
        self._timeout = max(1.0, float(timeout_seconds))
        self._keep_alive_seconds = keep_alive_seconds
        self._observer = OllamaCallObserver(
            lane="research_decision",
            model=self._model,
            num_ctx=num_ctx,
            num_predict=num_predict,
            metrics_callback=metrics_callback,
        )

    def decide(self, *, text: str, context: dict[str, Any]) -> ResearchDecision | None:
        if not self._base_url or not self._model:
            return None
        recent_turns = self._compact_recent_turns(context)
        prompt = (
            "You are Jarvis's read-only research router. Return strict JSON only.\n"
            "Choose research when current/fresh information, source verification, or niche facts are needed, "
            "or when you are not at least 0.75 confident you can answer accurately from stable knowledge.\n"
            "Choose direct for greetings, creative work, opinions, ordinary recipes, and basic stable facts.\n"
            "Choose clarify only when the information question is too ambiguous to form a useful search.\n"
            "Never convert this into a household action and never include private session details in the query.\n"
            "Schema: {\"mode\":\"direct|research|clarify\",\"query\":\"minimal search query or null\","
            "\"confidence\":0.0,\"reason\":\"short reason\"}\n"
            f"Recent conversation: {recent_turns}\n"
            f"User question: {str(text or '').strip()}\n"
        )
        request_payload: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": self._observer.options(temperature=0.0),
        }
        if self._keep_alive_seconds is not None:
            request_payload["keep_alive"] = f"{int(max(self._keep_alive_seconds, 1.0))}s"
        try:
            response = httpx.post(
                f"{self._base_url}/api/generate",
                json=request_payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
            parsed = self._first_json_object(str(payload.get("response") or ""))
        except Exception as exc:
            self._observer.record(prompt=prompt, outcome="error", error_type=type(exc).__name__)
            return None
        self._observer.record(prompt=prompt, response_payload=payload, outcome="success")
        if not isinstance(parsed, dict):
            return None
        mode = str(parsed.get("mode") or "").strip().lower()
        if mode not in {"direct", "research", "clarify"}:
            return None
        query = re.sub(r"\s+", " ", str(parsed.get("query") or "")).strip()[:240] or None
        if mode == "research" and query is None:
            query = re.sub(r"\s+", " ", str(text or "")).strip()[:240] or None
        confidence_raw = parsed.get("confidence")
        confidence = float(confidence_raw) if isinstance(confidence_raw, (int, float)) else 0.5
        return ResearchDecision(
            mode=mode,
            query=query,
            confidence=max(0.0, min(confidence, 1.0)),
            reason=str(parsed.get("reason") or "model_research_decision").strip()[:240],
        )

    def status(self) -> dict[str, Any]:
        return self._observer.status()

    @staticmethod
    def _compact_recent_turns(context: dict[str, Any]) -> str:
        turns = context.get("recent_turns")
        if not isinstance(turns, list):
            working = context.get("working_context")
            turns = working.get("recent_turns") if isinstance(working, dict) else None
        if not isinstance(turns, list):
            return "(none)"
        values: list[str] = []
        for turn in turns[-4:]:
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("role") or "turn").strip().lower()
            value = re.sub(r"\s+", " ", str(turn.get("text") or "")).strip()[:160]
            if value:
                values.append(f"{role}: {value}")
        return " | ".join(values) or "(none)"

    @staticmethod
    def _first_json_object(value: str) -> dict[str, Any] | None:
        cleaned = str(value or "").strip()
        if not cleaned:
            return None
        try:
            parsed = json.loads(cleaned)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
