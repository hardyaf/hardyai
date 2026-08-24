from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from app.context.types import EntityRegistry, TrackedEntity


@dataclass(frozen=True)
class ResolvedReference:
    entity: TrackedEntity
    reason: str


class ReferenceResolver:
    def is_deictic_reference(self, *, value: str, entity_type: str) -> bool:
        normalized = self._normalized(value)
        if not normalized:
            return False
        if entity_type == "list":
            trimmed = self._strip_list_words(normalized)
            return trimmed in {"it", "that", "this", "same", "same list", "that list", "this list"}
        if entity_type == "switch":
            exact = {
                "it",
                "that",
                "this",
                "that one",
                "this one",
                "the one",
                "the light",
                "that light",
                "this light",
                "the lamp",
                "that lamp",
                "this lamp",
                "same",
            }
            if normalized in exact:
                return True
            tokens = normalized.split()
            if not tokens or tokens[0] not in {"it", "that", "this"}:
                return False
            trailing = {"back", "again", "one", "light", "lamp", "same"}
            return all(token in trailing for token in tokens[1:])
        return normalized in {"it", "that", "this", "same", "that one", "this one"}

    def resolve_reference(
        self,
        *,
        value: str,
        registry: EntityRegistry,
        domain: str,
        entity_type: str,
        deictic_only: bool = False,
    ) -> ResolvedReference | None:
        normalized = self._normalized(value)
        if not normalized:
            return None

        if self.is_deictic_reference(value=value, entity_type=entity_type):
            candidate = self._best_candidate(
                registry=registry,
                domain=domain,
                entity_type=entity_type,
            )
            if candidate is None:
                return None
            return ResolvedReference(entity=candidate, reason="deictic_reference")

        if deictic_only:
            return None

        for entity in registry.entities:
            if entity.domain != domain or entity.entity_type != entity_type:
                continue
            if self._normalized(entity.display_name) == normalized:
                return ResolvedReference(entity=entity, reason="exact_name_match")
            aliases = {self._normalized(alias) for alias in entity.aliases if alias}
            if normalized in aliases:
                return ResolvedReference(entity=entity, reason="alias_match")

        alias_hit = registry.alias_map.get(normalized)
        if alias_hit:
            for entity in registry.entities:
                if entity.domain != domain or entity.entity_type != entity_type:
                    continue
                if str(entity.display_name).strip() == alias_hit:
                    return ResolvedReference(entity=entity, reason="registry_alias_map")
        return None

    @staticmethod
    def _best_candidate(
        *,
        registry: EntityRegistry,
        domain: str,
        entity_type: str,
    ) -> TrackedEntity | None:
        candidates = [
            entity
            for entity in registry.entities
            if entity.domain == domain and entity.entity_type == entity_type and str(entity.display_name).strip()
        ]
        if not candidates:
            return None
        candidates.sort(
            key=lambda entity: (
                float(entity.salience),
                ReferenceResolver._sort_timestamp(entity.last_confirmed_at),
            ),
            reverse=True,
        )
        return candidates[0]

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
    def _normalized(value: str) -> str:
        cleaned = re.sub(r"[^a-z0-9\s_-]+", "", str(value or "").strip().lower())
        cleaned = cleaned.replace("_", " ")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    @staticmethod
    def _strip_list_words(value: str) -> str:
        normalized = re.sub(r"^(?:my|the|our)\s+", "", value.strip())
        normalized = re.sub(r"\s+list$", "", normalized).strip()
        return normalized

