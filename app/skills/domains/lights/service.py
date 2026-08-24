from __future__ import annotations

import re
from datetime import datetime, timezone

from app.db.sqlite_store import SQLiteStore
from app.skills.domains.lights.storage import InMemoryLightsStorage, LightsStorage, SQLiteLightsStorage


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HomeService:
    def __init__(
        self,
        sqlite_store: SQLiteStore | None = None,
        storage: LightsStorage | None = None,
        default_switch_names: list[str] | None = None,
    ) -> None:
        if storage is not None:
            self._storage = storage
        elif sqlite_store is not None:
            self._storage = SQLiteLightsStorage(sqlite_store=sqlite_store)
        else:
            self._storage = InMemoryLightsStorage()
        defaults = default_switch_names or []
        for name in defaults:
            self.ensure_switch(name=name, room_name=None, default_state="off")

    @staticmethod
    def _normalize_switch_name(value: str) -> str:
        normalized = value.strip().lower()
        normalized = re.sub(r"[^a-z0-9\s_-]+", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        normalized = re.sub(r"^the\s+", "", normalized)
        normalized = re.sub(r"\blights\b", "light", normalized)
        normalized = re.sub(r"\blamps\b", "lamp", normalized)
        return normalized.strip()

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return {token for token in value.split() if token}

    def _existing_switch_names(self) -> list[str]:
        return [str(item["name"]) for item in self._storage.list_switches()]

    def _resolve_switch_name(self, requested_name: str) -> tuple[str, bool]:
        normalized_request = self._normalize_switch_name(requested_name)
        if not normalized_request:
            return "", False

        existing = self._existing_switch_names()
        if normalized_request in existing:
            return normalized_request, True

        req_tokens = self._tokens(normalized_request)
        best_name = normalized_request
        best_score = 0.0
        for candidate in existing:
            candidate_norm = self._normalize_switch_name(candidate)
            candidate_tokens = self._tokens(candidate_norm)
            if not candidate_tokens:
                continue
            overlap = len(req_tokens & candidate_tokens)
            union = len(req_tokens | candidate_tokens)
            if union == 0:
                continue
            jaccard = overlap / union
            request_coverage = overlap / len(req_tokens) if req_tokens else 0.0
            score = max(jaccard, request_coverage)
            if req_tokens and req_tokens.issubset(candidate_tokens):
                score = max(score, 0.85)
            if score > best_score:
                best_score = score
                best_name = candidate
        if best_score >= 0.6:
            return best_name, True
        return normalized_request, False

    @classmethod
    def _is_all_lights_target(cls, normalized_name: str) -> bool:
        target = cls._normalize_switch_name(normalized_name)
        return target in {"all light", "all", "every light", "lights", "light"}

    def _suggest_switches(self, requested_name: str, limit: int = 3) -> list[str]:
        normalized_request = self._normalize_switch_name(requested_name)
        if not normalized_request:
            return []
        req_tokens = self._tokens(normalized_request)
        scored: list[tuple[float, str]] = []
        for candidate in self._existing_switch_names():
            candidate_norm = self._normalize_switch_name(candidate)
            candidate_tokens = self._tokens(candidate_norm)
            if not candidate_tokens:
                continue
            overlap = len(req_tokens & candidate_tokens)
            union = len(req_tokens | candidate_tokens)
            if union == 0:
                continue
            score = overlap / union
            if score > 0:
                scored.append((score, candidate))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [name for _, name in scored[:limit]]

    def ensure_switch(self, name: str, room_name: str | None, default_state: str = "off") -> None:
        normalized_name = self._normalize_switch_name(name)
        if not normalized_name:
            return
        state = default_state.strip().lower()
        if state not in {"on", "off"}:
            state = "off"
        existing = self._storage.get_switch(normalized_name)
        if existing is None:
            self._storage.upsert_switch(
                name=normalized_name,
                room_name=room_name,
                state=state,
                updated_at=_utc_now(),
            )

    def set_switch(
        self,
        switch_name: str,
        action: str,
        source_interface: str | None = None,
        requested_by_user_id: str | None = None,
    ) -> dict[str, object]:
        normalized_input = self._normalize_switch_name(switch_name)
        normalized_name, matched_existing = self._resolve_switch_name(switch_name)
        normalized_action = action.strip().lower()
        if normalized_action not in {"on", "off"}:
            return {
                "status": "error",
                "message": "Action must be `on` or `off`.",
            }
        if self._is_all_lights_target(normalized_input):
            targets = sorted(self._existing_switch_names())
            if not targets:
                return {
                    "status": "unknown_switch",
                    "message": "No house switches are configured yet.",
                    "input_switch_name": switch_name.strip().lower(),
                    "resolved_switch_name": "all lights",
                    "available_switches": [],
                    "suggestions": [],
                }
            for target_name in targets:
                timestamp = _utc_now()
                self._storage.upsert_switch(
                    name=target_name,
                    room_name=None,
                    state=normalized_action,
                    updated_at=timestamp,
                )
                self._storage.insert_action_log(
                    timestamp=timestamp,
                    switch_name=target_name,
                    action=normalized_action,
                    state_after=normalized_action,
                    source_interface=source_interface,
                    requested_by_user_id=requested_by_user_id,
                )
            switches = {
                str(entry["name"]): entry.get("state")
                for entry in self._storage.list_switches()
            }
            return {
                "status": "ok",
                "switch_name": "all lights",
                "input_switch_name": switch_name.strip().lower(),
                "matched_existing": True,
                "scope": "all",
                "action": normalized_action,
                "affected_switches": targets,
                "affected_count": len(targets),
                "switches": switches,
            }

        if not matched_existing:
            return {
                "status": "unknown_switch",
                "message": (
                    f"I could not find a known switch for `{switch_name.strip()}`. "
                    "Use one of the configured house switches."
                ),
                "input_switch_name": switch_name.strip().lower(),
                "resolved_switch_name": normalized_name,
                "available_switches": sorted(self._existing_switch_names()),
                "suggestions": self._suggest_switches(switch_name),
            }
        timestamp = _utc_now()
        self._storage.upsert_switch(
            name=normalized_name,
            room_name=None,
            state=normalized_action,
            updated_at=timestamp,
        )
        self._storage.insert_action_log(
            timestamp=timestamp,
            switch_name=normalized_name,
            action=normalized_action,
            state_after=normalized_action,
            source_interface=source_interface,
            requested_by_user_id=requested_by_user_id,
        )
        switches = {
            str(entry["name"]): entry.get("state")
            for entry in self._storage.list_switches()
        }
        return {
            "status": "ok",
            "switch_name": normalized_name,
            "input_switch_name": switch_name.strip().lower(),
            "matched_existing": matched_existing,
            "action": normalized_action,
            "switches": switches,
        }

    def list_switches(self) -> list[dict[str, object]]:
        return self._storage.list_switches()

    def recent_actions(self, limit: int = 50) -> list[dict[str, object]]:
        return self._storage.recent_actions(limit=limit)

    def reset(self) -> None:
        self._storage.clear()
