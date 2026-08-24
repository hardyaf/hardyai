from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.context.serialization import deserialize_session_context, serialize_session_context
from app.context.types import EntityRegistry, TrackedEntity

if TYPE_CHECKING:
    from app.core.session_store import SessionRecord


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EntityRegistryManager:
    def __init__(self, *, max_entities: int = 96) -> None:
        self._max_entities = max(8, int(max_entities))

    def get_registry(self, *, session: "SessionRecord") -> EntityRegistry:
        state = deserialize_session_context(session.context_reference)
        return state.entity_registry

    def latest_entity_display_name(
        self,
        *,
        session: "SessionRecord",
        domain: str,
        entity_type: str,
    ) -> str | None:
        registry = self.get_registry(session=session)
        entity = self._best_entity(
            registry=registry,
            domain=domain,
            entity_type=entity_type,
        )
        if entity is None:
            return None
        name = str(entity.display_name or "").strip()
        return name or None

    def record_entities(
        self,
        *,
        session: "SessionRecord",
        entities: list[dict[str, Any]],
    ) -> dict[str, int | bool]:
        if not entities:
            return {"updated": False, "upserted_count": 0, "total_entities": 0}

        state = deserialize_session_context(session.context_reference)
        upserted_count = 0
        updated = False
        for payload in entities:
            if not isinstance(payload, dict):
                continue
            if self._upsert_entity(
                registry=state.entity_registry,
                payload=payload,
            ):
                updated = True
                upserted_count += 1

        if not updated:
            return {
                "updated": False,
                "upserted_count": 0,
                "total_entities": len(state.entity_registry.entities),
            }

        self._prune_registry(state.entity_registry)
        serialized = serialize_session_context(state)
        merged = dict(session.context_reference)
        merged.update(serialized)
        session.context_reference = merged
        return {
            "updated": True,
            "upserted_count": upserted_count,
            "total_entities": len(state.entity_registry.entities),
        }

    def _upsert_entity(
        self,
        *,
        registry: EntityRegistry,
        payload: dict[str, Any],
    ) -> bool:
        domain = str(payload.get("domain") or "").strip().lower()
        entity_type = str(payload.get("entity_type") or "").strip().lower()
        display_name = str(payload.get("display_name") or "").strip()
        if not domain or not entity_type or not display_name:
            return False

        aliases = self._normalize_aliases(display_name=display_name, aliases=payload.get("aliases"))
        salience = self._coerce_salience(payload.get("salience"))
        last_confirmed_at = str(payload.get("last_confirmed_at") or "").strip() or _utc_now()
        resolution_hints = payload.get("resolution_hints")
        if not isinstance(resolution_hints, dict):
            resolution_hints = {}
        entity_id = str(payload.get("entity_id") or "").strip() or None

        existing = self._find_existing(
            registry=registry,
            domain=domain,
            entity_type=entity_type,
            display_name=display_name,
            aliases=aliases,
        )
        if existing is None:
            registry.entities.append(
                TrackedEntity(
                    domain=domain,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    display_name=display_name,
                    aliases=aliases,
                    salience=salience,
                    last_confirmed_at=last_confirmed_at,
                    resolution_hints=dict(resolution_hints),
                )
            )
            self._sync_alias_map(registry)
            return True

        changed = False
        if entity_id and existing.entity_id != entity_id:
            existing.entity_id = entity_id
            changed = True
        if existing.display_name != display_name:
            existing.display_name = display_name
            changed = True
        merged_aliases = sorted({*(existing.aliases or []), *aliases})
        if merged_aliases != list(existing.aliases):
            existing.aliases = merged_aliases
            changed = True
        next_salience = max(float(existing.salience), float(salience))
        if abs(next_salience - float(existing.salience)) > 1e-6:
            existing.salience = next_salience
            changed = True
        if existing.last_confirmed_at != last_confirmed_at:
            existing.last_confirmed_at = last_confirmed_at
            changed = True
        if resolution_hints:
            merged_hints = dict(existing.resolution_hints)
            merged_hints.update(resolution_hints)
            if merged_hints != existing.resolution_hints:
                existing.resolution_hints = merged_hints
                changed = True

        if changed:
            self._sync_alias_map(registry)
        return changed

    @staticmethod
    def _find_existing(
        *,
        registry: EntityRegistry,
        domain: str,
        entity_type: str,
        display_name: str,
        aliases: list[str],
    ) -> TrackedEntity | None:
        target = EntityRegistryManager._normalized(display_name)
        alias_set = {EntityRegistryManager._normalized(item) for item in aliases if item}
        for entity in registry.entities:
            if entity.domain != domain or entity.entity_type != entity_type:
                continue
            candidate = EntityRegistryManager._normalized(entity.display_name)
            if candidate == target:
                return entity
            candidate_aliases = {EntityRegistryManager._normalized(item) for item in entity.aliases if item}
            if target and target in candidate_aliases:
                return entity
            if alias_set and candidate and candidate in alias_set:
                return entity
            if alias_set and candidate_aliases & alias_set:
                return entity
        return None

    def _prune_registry(self, registry: EntityRegistry) -> None:
        if len(registry.entities) <= self._max_entities:
            return
        ranked = sorted(
            registry.entities,
            key=lambda item: (
                float(item.salience),
                self._sort_timestamp(item.last_confirmed_at),
            ),
            reverse=True,
        )
        registry.entities = ranked[: self._max_entities]
        self._sync_alias_map(registry)

    @staticmethod
    def _best_entity(
        *,
        registry: EntityRegistry,
        domain: str,
        entity_type: str,
    ) -> TrackedEntity | None:
        candidates = [
            item
            for item in registry.entities
            if item.domain == domain and item.entity_type == entity_type and str(item.display_name).strip()
        ]
        if not candidates:
            return None
        candidates.sort(
            key=lambda item: (
                float(item.salience),
                EntityRegistryManager._sort_timestamp(item.last_confirmed_at),
            ),
            reverse=True,
        )
        return candidates[0]

    @staticmethod
    def _normalize_aliases(*, display_name: str, aliases: Any) -> list[str]:
        merged: set[str] = set()
        merged.add(EntityRegistryManager._normalized(display_name))
        if isinstance(aliases, list):
            for item in aliases:
                normalized = EntityRegistryManager._normalized(item)
                if normalized:
                    merged.add(normalized)
        base = EntityRegistryManager._normalized(display_name)
        if base.endswith(" list"):
            merged.add(base[: -len(" list")].strip())
        if base.startswith("the "):
            merged.add(base[4:].strip())
        if base.startswith("my "):
            merged.add(base[3:].strip())
        return sorted(item for item in merged if item)

    @staticmethod
    def _normalized(value: Any) -> str:
        cleaned = re.sub(r"[^a-z0-9\s_-]+", "", str(value or "").strip().lower())
        cleaned = cleaned.replace("_", " ")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    @staticmethod
    def _coerce_salience(raw: Any) -> float:
        if isinstance(raw, (int, float)):
            return max(0.0, min(float(raw), 1.0))
        if isinstance(raw, str):
            cleaned = raw.strip()
            if cleaned:
                try:
                    return max(0.0, min(float(cleaned), 1.0))
                except ValueError:
                    return 0.5
        return 0.5

    @staticmethod
    def _sort_timestamp(value: str | None) -> float:
        if not isinstance(value, str) or not value.strip():
            return 0.0
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return 0.0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()

    @staticmethod
    def _sync_alias_map(registry: EntityRegistry) -> None:
        alias_map: dict[str, str] = {}
        for entity in registry.entities:
            display = str(entity.display_name or "").strip()
            if not display:
                continue
            for alias in entity.aliases:
                cleaned = EntityRegistryManager._normalized(alias)
                if cleaned:
                    alias_map[cleaned] = display
        registry.alias_map = alias_map

