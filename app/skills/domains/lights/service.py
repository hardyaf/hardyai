from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from app.db.sqlite_store import SQLiteStore
from app.skills.domains.lights.storage import InMemoryLightsStorage, LightsStorage, SQLiteLightsStorage


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HomeService:
    _DEVICE_REF_PREFIX = "device_v1:"
    _MAX_ALIAS_HINTS = 8
    _MAX_CANDIDATES = 3

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

    @classmethod
    def _device_ref(cls, name: str) -> str:
        normalized = cls._normalize_switch_name(name)
        digest = hashlib.sha256(f"home-device-v1\n{normalized}".encode("utf-8")).hexdigest()
        return f"{cls._DEVICE_REF_PREFIX}{digest[:32]}"

    @classmethod
    def _alias_hints(cls, *, name: str, room_name: str | None) -> list[str]:
        canonical = cls._normalize_switch_name(name)
        aliases: set[str] = set()
        if canonical:
            aliases.add(canonical)
            without_kind = re.sub(r"\s+(?:light|lamp|switch)$", "", canonical).strip()
            if without_kind:
                aliases.add(without_kind)
            without_test = re.sub(r"\btest\b", " ", canonical)
            without_test = re.sub(r"\s+", " ", without_test).strip()
            if without_test:
                aliases.add(without_test)
                aliases.add(
                    re.sub(r"\s+(?:light|lamp|switch)$", "", without_test).strip()
                )
            location_tokens = [
                token
                for token in canonical.split()
                if token not in {"ceiling", "floor", "light", "lamp", "switch", "test"}
            ]
            if location_tokens:
                aliases.add(location_tokens[0])
        normalized_room = cls._normalize_switch_name(room_name or "")
        if normalized_room:
            aliases.add(normalized_room)
        aliases.discard("")
        aliases.discard(canonical)
        return sorted(aliases)[: cls._MAX_ALIAS_HINTS]

    @classmethod
    def _device_projection(cls, row: dict[str, Any]) -> dict[str, Any]:
        name = cls._normalize_switch_name(str(row.get("name") or ""))
        state = str(row.get("state") or "off").strip().casefold()
        projected: dict[str, Any] = {
            "device_ref": cls._device_ref(name),
            "name": name,
            "state": state if state in {"on", "off"} else "unknown",
            "alias_hints": cls._alias_hints(
                name=name,
                room_name=str(row.get("room_name") or "").strip() or None,
            ),
        }
        room_name = str(row.get("room_name") or "").strip()
        if room_name:
            projected["room_name"] = room_name[:100]
        updated_at = str(row.get("updated_at") or "").strip()
        if updated_at:
            projected["updated_at"] = updated_at[:64]
        return projected

    def list_devices(self, *, limit: int = 100) -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit), 100))
        rows = self._storage.list_switches()
        devices = [self._device_projection(row) for row in rows[:bounded_limit]]
        return {
            "devices": devices,
            "source": "local_simulated_state",
            "simulated": True,
            "truncated": len(rows) > bounded_limit,
        }

    def canonicalize_device_selector(
        self,
        *,
        device_ref: str | None = None,
        name: str | None = None,
    ) -> dict[str, str]:
        normalized_ref = str(device_ref or "").strip()
        normalized_name = self._normalize_switch_name(str(name or ""))
        resolved = self._resolve_device(device_ref=normalized_ref, name=normalized_name)
        device = resolved.get("device")
        if isinstance(device, dict) and str(device.get("device_ref") or "").strip():
            return {"device_ref": str(device["device_ref"])}
        if normalized_ref:
            return {"device_ref": normalized_ref}
        return {"name": normalized_name}

    def get_device_state(
        self,
        *,
        device_ref: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        resolved = self._resolve_device(device_ref=device_ref, name=name)
        device = resolved.get("device")
        match_status = str(resolved.get("match_status") or "not_found")
        candidates = list(resolved.get("candidates") or [])[: self._MAX_CANDIDATES]
        payload: dict[str, Any] = {
            "candidates": candidates,
            "match_status": match_status,
            "source": "local_simulated_state",
            "simulated": True,
        }
        if isinstance(device, dict):
            payload["device"] = device
            return {
                "status": "ok",
                "message": (
                    f"The simulated state for {device['name']} is {device['state']}."
                ),
                "payload": payload,
            }
        return {
            "status": "needs_input",
            "message": (
                "That device reference is stale; choose a currently configured device."
                if match_status == "stale_reference"
                else "Please choose one exact configured device."
            ),
            "missing_fields": ["device_ref"],
            "payload": payload,
        }

    def _resolve_device(
        self,
        *,
        device_ref: str | None,
        name: str | None,
    ) -> dict[str, Any]:
        devices = [self._device_projection(row) for row in self._storage.list_switches()]
        normalized_ref = str(device_ref or "").strip()
        if normalized_ref:
            matches = [item for item in devices if item["device_ref"] == normalized_ref]
            if len(matches) == 1:
                return {"device": matches[0], "candidates": [], "match_status": "exact_ref"}
            return {
                "device": None,
                "candidates": [self._device_candidate(item) for item in devices[: self._MAX_CANDIDATES]],
                "match_status": "stale_reference",
            }

        normalized_name = self._normalize_switch_name(str(name or ""))
        exact = [item for item in devices if item["name"] == normalized_name]
        if len(exact) == 1:
            return {"device": exact[0], "candidates": [], "match_status": "exact_name"}

        alias_matches = [
            item
            for item in devices
            if normalized_name and normalized_name in item["alias_hints"]
        ]
        if len(alias_matches) == 1:
            return {
                "device": alias_matches[0],
                "candidates": [],
                "match_status": "unique_alias",
            }
        if len(alias_matches) > 1:
            ordered = sorted(alias_matches, key=lambda item: str(item["name"]))
            return {
                "device": None,
                "candidates": [
                    self._device_candidate(item)
                    for item in ordered[: self._MAX_CANDIDATES]
                ],
                "match_status": "ambiguous_alias",
            }

        requested_tokens = self._tokens(normalized_name)
        suggestions: list[tuple[int, str, dict[str, Any]]] = []
        for item in devices:
            candidate_tokens = self._tokens(str(item["name"]))
            overlap = len(requested_tokens & candidate_tokens)
            if overlap:
                suggestions.append((-overlap, str(item["name"]), item))
        suggestions.sort(key=lambda item: (item[0], item[1]))
        return {
            "device": None,
            "candidates": [
                self._device_candidate(item)
                for _, _, item in suggestions[: self._MAX_CANDIDATES]
            ],
            "match_status": "not_found",
        }

    @staticmethod
    def _device_candidate(device: dict[str, Any]) -> dict[str, Any]:
        return {
            "device_ref": str(device["device_ref"]),
            "name": str(device["name"]),
            "alias_hints": list(device.get("alias_hints") or [])[:8],
        }

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

    def set_device_state(
        self,
        *,
        device_ref: str | None,
        state: str,
        source_interface: str | None,
        requested_by_user_id: str | None,
        operation_id: str,
        arguments_hash: str,
    ) -> dict[str, Any]:
        desired_state = str(state or "").strip().casefold()
        if desired_state not in {"on", "off"}:
            return {
                "status": "policy_denied",
                "message": "The desired Home state is not supported.",
                "denial_reason": "home_device_state_invalid",
            }
        resolved = self._resolve_device(device_ref=device_ref, name=None)
        device = resolved.get("device")
        if not isinstance(device, dict):
            return {
                "status": "needs_input",
                "message": "That device reference is stale; choose a currently configured device.",
                "missing_fields": ["device_ref"],
                "payload": {
                    "candidates": list(resolved.get("candidates") or [])[: self._MAX_CANDIDATES],
                    "match_status": str(resolved.get("match_status") or "stale_reference"),
                    "changed": False,
                    "idempotent_replay": False,
                    "source": "local_simulated_state",
                    "simulated": True,
                },
            }
        try:
            mutation = self._storage.set_device_state(
                name=str(device["name"]),
                state=desired_state,
                timestamp=_utc_now(),
                source_interface=source_interface,
                requested_by_user_id=requested_by_user_id,
                operation_id=operation_id,
                arguments_hash=arguments_hash,
            )
        except ValueError as exc:
            code = str(exc).strip().casefold()
            if code == "home_operation_id_conflict":
                return {
                    "status": "policy_denied",
                    "message": "That Home operation identity conflicts with an earlier call.",
                    "denial_reason": code,
                }
            return {
                "status": "error",
                "message": "The simulated Home state could not be updated safely.",
            }
        projected = self._device_projection(dict(mutation["switch"]))
        return {
            "status": "ok",
            "message": f"The simulated state for {projected['name']} is {projected['state']}.",
            "payload": {
                "device": projected,
                "candidates": [],
                "match_status": "exact_ref",
                "changed": bool(mutation.get("changed")),
                "idempotent_replay": bool(mutation.get("idempotent_replay")),
                "source": "local_simulated_state",
                "simulated": True,
            },
        }

    def list_switches(self) -> list[dict[str, object]]:
        return self._storage.list_switches()

    def recent_actions(self, limit: int = 50) -> list[dict[str, object]]:
        return self._storage.recent_actions(limit=limit)

    def reset(self) -> None:
        self._storage.clear()
