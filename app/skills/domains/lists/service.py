from __future__ import annotations

import difflib
import re
from datetime import datetime, timezone

from app.db.sqlite_store import SQLiteStore
from app.skills.domains.lists.storage import InMemoryListsStorage, ListsStorage, SQLiteListsStorage


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ListsService:
    _LIST_ALIASES = {
        "grocery": "groceries",
        "groceries": "groceries",
        "shopping": "groceries",
        "shopping list": "groceries",
        "grocery list": "groceries",
        "todo": "to-do",
        "to do": "to-do",
        "to-do": "to-do",
    }
    _DEICTIC_LIST_REFERENCES = {"it", "that", "this", "same", "same list", "that list", "this list"}
    _ALL_ITEMS_ALIASES = {
        "all",
        "all item",
        "all items",
        "everything",
        "entire list",
        "whole list",
        "entire thing",
        "whole thing",
        "the whole list",
        "the entire list",
        "everything on the list",
    }
    _COMPLETION_MODE_ALIASES = {
        "done": "done",
        "mark done": "done",
        "mark as done": "done",
        "complete": "done",
        "completed": "done",
        "mark complete": "done",
        "mark as complete": "done",
        "check": "done",
        "checked": "done",
        "check off": "done",
        "checked off": "done",
        "remove": "remove",
        "delete": "remove",
        "clear": "remove",
    }

    def __init__(
        self,
        default_list_names: list[str] | None = None,
        sqlite_store: SQLiteStore | None = None,
        storage: ListsStorage | None = None,
        default_owner_user_id: str = "all",
    ) -> None:
        if storage is not None:
            self._storage = storage
        elif sqlite_store is not None:
            self._storage = SQLiteListsStorage(sqlite_store=sqlite_store)
        else:
            self._storage = InMemoryListsStorage()
        self._default_owner_user_id = (default_owner_user_id or "all").strip().lower() or "all"

        defaults = default_list_names or []
        for name in defaults:
            self.ensure_list(name=name, created_by="system")

    @classmethod
    def _normalize_list_name(cls, value: str) -> str:
        normalized = value.strip()
        normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", normalized)
        normalized = normalized.lower()
        normalized = re.sub(r"[^a-z0-9\s_-]+", "", normalized)
        normalized = normalized.replace("_", " ")
        normalized = re.sub(r"\s+", " ", normalized).strip()
        normalized = re.sub(r"^(?:my|the|our|a|an)\s+", "", normalized)
        normalized = re.sub(r"\s+lists?$", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if normalized in cls._LIST_ALIASES:
            normalized = cls._LIST_ALIASES[normalized]
        return normalized

    @classmethod
    def normalize_list_name(cls, value: str) -> str:
        """Return the domain's canonical collection name without resolving a target."""

        return cls._normalize_list_name(value)

    @staticmethod
    def _compact_list_key(value: str) -> str:
        return re.sub(r"[\s_-]+", "", value.strip().lower())

    @staticmethod
    def _normalize_token(token: str) -> str:
        if token.endswith("ies") and len(token) > 3:
            return f"{token[:-3]}y"
        if token.endswith("s") and len(token) > 3:
            return token[:-1]
        return token

    @classmethod
    def _tokens(cls, value: str) -> set[str]:
        parts = re.split(r"[\s-]+", value)
        tokens = {cls._normalize_token(part) for part in parts if part}
        compact = cls._compact_list_key(value)
        if compact:
            tokens.add(compact)
        return tokens

    @classmethod
    def _list_similarity_score(cls, requested: str, candidate: str) -> float:
        requested = requested.strip().lower()
        candidate = candidate.strip().lower()
        if not requested or not candidate:
            return 0.0
        if requested == candidate:
            return 1.0

        request_tokens = cls._tokens(requested)
        candidate_tokens = cls._tokens(candidate)
        token_score = 0.0
        if request_tokens and candidate_tokens:
            overlap = len(request_tokens & candidate_tokens)
            union = len(request_tokens | candidate_tokens)
            if union > 0:
                token_score = overlap / union

        compact_requested = cls._compact_list_key(requested)
        compact_candidate = cls._compact_list_key(candidate)
        compact_ratio = 0.0
        if compact_requested and compact_candidate:
            if compact_requested == compact_candidate:
                compact_ratio = 1.0
            else:
                compact_ratio = difflib.SequenceMatcher(
                    None,
                    compact_requested,
                    compact_candidate,
                ).ratio()

        text_ratio = difflib.SequenceMatcher(None, requested, candidate).ratio()
        startswith_score = 0.0
        if compact_requested and compact_candidate:
            if compact_candidate.startswith(compact_requested) or compact_requested.startswith(compact_candidate):
                startswith_score = 0.82

        return max(token_score, compact_ratio, text_ratio, startswith_score)

    @classmethod
    def _normalize_item_name(cls, value: str) -> str:
        normalized = value.strip().lower()
        normalized = re.sub(r"[^a-z0-9\s_-]+", " ", normalized)
        normalized = normalized.replace("_", " ")
        normalized = re.sub(r"\s+", " ", normalized).strip()
        normalized = re.sub(r"^(?:the|a|an)\s+", "", normalized)
        return normalized

    @classmethod
    def _is_all_items_phrase(cls, value: str) -> bool:
        normalized = cls._normalize_item_name(value)
        return normalized in cls._ALL_ITEMS_ALIASES

    @classmethod
    def _item_tokens(cls, value: str) -> set[str]:
        compact = cls._compact_list_key(value)
        tokens = {cls._normalize_token(token) for token in re.split(r"[\s-]+", value) if token}
        if compact:
            tokens.add(compact)
        return tokens

    @classmethod
    def _item_similarity_score(cls, requested: str, candidate: str) -> float:
        requested_norm = cls._normalize_item_name(requested)
        candidate_norm = cls._normalize_item_name(candidate)
        if not requested_norm or not candidate_norm:
            return 0.0
        if requested_norm == candidate_norm:
            return 1.0

        requested_tokens = cls._item_tokens(requested_norm)
        candidate_tokens = cls._item_tokens(candidate_norm)
        token_score = 0.0
        if requested_tokens and candidate_tokens:
            overlap = len(requested_tokens & candidate_tokens)
            union = len(requested_tokens | candidate_tokens)
            if union > 0:
                token_score = overlap / union

        compact_requested = cls._compact_list_key(requested_norm)
        compact_candidate = cls._compact_list_key(candidate_norm)
        compact_score = difflib.SequenceMatcher(None, compact_requested, compact_candidate).ratio()
        text_score = difflib.SequenceMatcher(None, requested_norm, candidate_norm).ratio()
        startswith_score = 0.0
        if compact_requested and compact_candidate:
            if compact_candidate.startswith(compact_requested) or compact_requested.startswith(compact_candidate):
                startswith_score = 0.82
        return max(token_score, compact_score, text_score, startswith_score)

    def _owner_user_id(self, owner_user_id: str | None = None) -> str:
        if owner_user_id and owner_user_id.strip():
            return owner_user_id.strip().lower()
        return self._default_owner_user_id

    def resolve_owner_for_list(self, *, list_name: str, preferred_owner_user_id: str | None) -> str:
        """Resolve a personal list first, then an explicitly shared household list.

        Seeded household lists live under owner ``all``. New user-created lists
        remain owned by the requesting identity, while existing shared lists keep
        their shared source-of-truth identity in receipts.
        """
        preferred = self._owner_user_id(preferred_owner_user_id)
        _, preferred_match = self._resolve_list_name(list_name, preferred)
        if preferred_match:
            return preferred
        if preferred != "all":
            _, shared_match = self._resolve_list_name(list_name, "all")
            shared_suggestions = self._scored_list_suggestions(
                list_name,
                "all",
                limit=1,
            )
            preferred_has_lists = bool(self._existing_list_names(preferred))
            shared_has_lists = bool(self._existing_list_names("all"))
            if shared_match or shared_suggestions or (not preferred_has_lists and shared_has_lists):
                return "all"
        return preferred

    def _existing_list_names(self, owner_user_id: str | None = None) -> list[str]:
        owner = self._owner_user_id(owner_user_id)
        return self._storage.list_names(owner_user_id=owner)

    def _resolve_list_name(self, requested_name: str, owner_user_id: str | None = None) -> tuple[str, bool]:
        normalized_request = self._normalize_list_name(requested_name)
        if not normalized_request:
            return "", False

        existing = self._existing_list_names(owner_user_id)
        if normalized_request in existing:
            return normalized_request, True

        compact_request = self._compact_list_key(normalized_request)
        compact_matches: list[str] = []
        for candidate in existing:
            if self._compact_list_key(candidate) == compact_request:
                compact_matches.append(candidate)
        if len(compact_matches) == 1:
            return compact_matches[0], True

        return normalized_request, False

    def _scored_list_suggestions(
        self,
        requested_name: str,
        owner_user_id: str | None = None,
        limit: int = 3,
        min_score: float = 0.45,
    ) -> list[tuple[str, float]]:
        normalized_request = self._normalize_list_name(requested_name)
        if not normalized_request:
            return []

        scored: list[tuple[str, float]] = []
        for candidate in self._existing_list_names(owner_user_id):
            score = self._list_similarity_score(normalized_request, candidate)
            if score >= min_score:
                scored.append((candidate, score))

        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[: max(1, limit)]

    def _suggest_lists(self, requested_name: str, owner_user_id: str | None = None, limit: int = 3) -> list[str]:
        return [name for name, _ in self._scored_list_suggestions(requested_name, owner_user_id, limit=limit)]

    @classmethod
    def _normalize_completion_mode(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = re.sub(r"[^a-z0-9\s_-]+", " ", str(value).strip().lower())
        normalized = normalized.replace("_", " ")
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if not normalized:
            return None
        return cls._COMPLETION_MODE_ALIASES.get(normalized)

    def _list_names(self, owner_user_id: str | None = None) -> list[str]:
        owner = self._owner_user_id(owner_user_id)
        return sorted(self._storage.list_names(owner_user_id=owner))

    def _list_items(self, list_name: str, owner_user_id: str | None = None) -> list[str]:
        owner = self._owner_user_id(owner_user_id)
        return self._storage.list_items(owner_user_id=owner, list_name=list_name)

    def _list_item_entries(self, list_name: str, owner_user_id: str | None = None) -> list[dict[str, object]]:
        owner = self._owner_user_id(owner_user_id)
        return self._storage.list_item_entries(owner_user_id=owner, list_name=list_name)

    def _unknown_list_result(
        self,
        *,
        list_name: str,
        resolved_list_name: str,
        matched_existing: bool,
        owner_user_id: str,
        message_suffix: str | None = None,
    ) -> dict[str, object]:
        scored_suggestions = self._scored_list_suggestions(list_name, owner_user_id, limit=3)
        suggestions = [name for name, _ in scored_suggestions]
        top_suggestion = suggestions[0] if suggestions else None
        top_score = scored_suggestions[0][1] if scored_suggestions else None
        base_message = f"I could not find a known list for `{list_name.strip()}`."
        if message_suffix:
            base_message = f"{base_message} {message_suffix.strip()}"
        return {
            "status": "unknown_list",
            "message": base_message,
            "input_list_name": list_name.strip().lower(),
            "resolved_list_name": resolved_list_name,
            "matched_existing": matched_existing,
            "available_lists": self._list_names(owner_user_id),
            "suggestions": suggestions,
            "suggested_list": top_suggestion,
            "suggestion_confidence": round(top_score, 3) if isinstance(top_score, float) else None,
        }

    def _resolve_item_match(
        self,
        *,
        requested_item: str,
        item_entries: list[dict[str, object]],
    ) -> tuple[dict[str, object] | None, list[str], float | None]:
        if not item_entries:
            return None, [], None

        normalized_request = self._normalize_item_name(requested_item)
        if not normalized_request:
            return None, [], None

        scored: list[tuple[dict[str, object], float]] = []
        for entry in item_entries:
            item_name = str(entry.get("item_name") or "").strip()
            if not item_name:
                continue
            score = self._item_similarity_score(normalized_request, item_name)
            scored.append((entry, score))

        scored.sort(
            key=lambda item: (
                -item[1],
                str(item[0].get("item_name") or "").lower(),
            )
        )
        if not scored:
            return None, [], None

        suggestions = [
            str(entry.get("item_name") or "").strip()
            for entry, _ in scored
            if str(entry.get("item_name") or "").strip()
        ][:3]
        best_entry, best_score = scored[0]
        best_name = str(best_entry.get("item_name") or "").strip()
        best_name_normalized = self._normalize_item_name(best_name)
        if normalized_request == best_name_normalized:
            return best_entry, suggestions, best_score

        second_score = scored[1][1] if len(scored) > 1 else 0.0
        if best_score >= 0.72:
            return best_entry, suggestions, best_score
        if best_score >= 0.62 and (best_score - second_score) >= 0.12:
            return best_entry, suggestions, best_score
        return None, suggestions, best_score

    def ensure_list(self, name: str, owner_user_id: str | None = None, created_by: str = "system") -> str:
        owner = self._owner_user_id(owner_user_id)
        normalized_name, _ = self._resolve_list_name(name, owner)
        if not normalized_name:
            return ""
        return self._storage.ensure_list(
            owner_user_id=owner,
            list_name=normalized_name,
            created_by=created_by,
            timestamp=_utc_now(),
        )

    @classmethod
    def _is_deictic_list_reference(cls, value: str) -> bool:
        normalized = cls._normalize_list_name(value)
        return normalized in cls._DEICTIC_LIST_REFERENCES

    def create_list(
        self,
        list_name: str,
        owner_user_id: str | None = None,
        created_by: str = "user",
    ) -> dict[str, object]:
        owner = self._owner_user_id(owner_user_id)
        normalized_list, matched_existing = self._resolve_list_name(list_name, owner)
        if not normalized_list or self._is_deictic_list_reference(normalized_list):
            return {
                "status": "needs_input",
                "message": "Please tell me the actual list name to create.",
                "missing_fields": ["list_name"],
            }

        exists_before = normalized_list in self._existing_list_names(owner)
        ensured = self.ensure_list(name=normalized_list, owner_user_id=owner, created_by=created_by)
        items = self._list_items(ensured, owner)
        list_record = self._storage.get_list_record(owner_user_id=owner, list_name=ensured) or {}
        return {
            "status": "ok",
            "list_name": ensured,
            "list_id": list_record.get("list_id"),
            "owner_user_id": owner,
            "input_list_name": list_name.strip().lower(),
            "matched_existing": matched_existing,
            "created": not exists_before,
            "count": len(items),
            "items": items,
            "list_names": self._list_names(owner),
            "suggestions": self._suggest_lists(list_name, owner),
            "message": (
                f"I created the `{ensured}` list."
                if not exists_before
                else f"The `{ensured}` list already exists."
            ),
        }

    def add_item(
        self,
        list_name: str,
        item_text: str,
        owner_user_id: str | None = None,
        added_by: str = "user",
        operation_id: str | None = None,
    ) -> dict[str, object]:
        owner = self._owner_user_id(owner_user_id)
        normalized_list, matched_existing = self._resolve_list_name(list_name, owner)
        normalized_item = item_text.strip()
        if not normalized_list:
            return {
                "status": "needs_input",
                "message": "List name is required.",
                "missing_fields": ["list_name"],
            }
        if not normalized_item:
            return {
                "status": "needs_input",
                "message": "Item text is required.",
                "missing_fields": ["item_text"],
            }

        exists = normalized_list in self._existing_list_names(owner)
        if not exists:
            return self._unknown_list_result(
                list_name=list_name,
                resolved_list_name=normalized_list,
                matched_existing=matched_existing,
                owner_user_id=owner,
                message_suffix="Create it first with `create <name> list`.",
            )

        added = self._storage.add_item(
            owner_user_id=owner,
            list_name=normalized_list,
            item_name=normalized_item,
            added_by=added_by,
            timestamp=_utc_now(),
            operation_id=operation_id,
        )
        if not added:
            return self._unknown_list_result(
                list_name=list_name,
                resolved_list_name=normalized_list,
                matched_existing=matched_existing,
                owner_user_id=owner,
            )

        items = self._list_items(normalized_list, owner)
        list_record = self._storage.get_list_record(owner_user_id=owner, list_name=normalized_list) or {}
        return {
            "status": "ok",
            "list_name": normalized_list,
            "list_id": list_record.get("list_id"),
            "item_id": added.get("item_id"),
            "owner_user_id": owner,
            "operation_id": operation_id,
            "idempotent_replay": bool(added.get("idempotent_replay")),
            "input_list_name": list_name.strip().lower(),
            "matched_existing": matched_existing,
            "item_text": normalized_item,
            "count": len(items),
            "items": items,
            "list_names": self._list_names(owner),
            "suggestions": self._suggest_lists(list_name, owner),
        }

    def get_items(self, list_name: str, owner_user_id: str | None = None) -> dict[str, object]:
        owner = self._owner_user_id(owner_user_id)
        normalized_list, matched_existing = self._resolve_list_name(list_name, owner)
        if not normalized_list:
            return {
                "status": "needs_input",
                "message": "List name is required.",
                "missing_fields": ["list_name"],
            }
        exists = normalized_list in self._existing_list_names(owner)
        if not exists:
            return self._unknown_list_result(
                list_name=list_name,
                resolved_list_name=normalized_list,
                matched_existing=matched_existing,
                owner_user_id=owner,
            )

        item_entries = self._list_item_entries(normalized_list, owner)
        list_record = self._storage.get_list_record(owner_user_id=owner, list_name=normalized_list) or {}
        items = [str(entry.get("item_name") or "") for entry in item_entries if str(entry.get("item_name") or "")]
        return {
            "status": "ok",
            "list_name": normalized_list,
            "list_id": list_record.get("list_id"),
            "owner_user_id": owner,
            "source_revision": list_record.get("updated_at"),
            "input_list_name": list_name.strip().lower(),
            "matched_existing": matched_existing,
            "count": len(items),
            "items": items,
            "item_entries": item_entries,
            "list_names": self._list_names(owner),
        }

    def delete_list(self, list_name: str, owner_user_id: str | None = None) -> dict[str, object]:
        owner = self._owner_user_id(owner_user_id)
        normalized_list, matched_existing = self._resolve_list_name(list_name, owner)
        if not normalized_list:
            return {
                "status": "needs_input",
                "message": "List name is required.",
                "missing_fields": ["list_name"],
            }
        exists = normalized_list in self._existing_list_names(owner)
        if not exists:
            return self._unknown_list_result(
                list_name=list_name,
                resolved_list_name=normalized_list,
                matched_existing=matched_existing,
                owner_user_id=owner,
            )

        item_count = len(self._list_items(normalized_list, owner))
        list_record = self._storage.get_list_record(owner_user_id=owner, list_name=normalized_list) or {}
        deleted = self._storage.delete_list(owner_user_id=owner, list_name=normalized_list)
        if not deleted:
            return {
                "status": "error",
                "message": f"I could not delete `{normalized_list}`.",
                "list_name": normalized_list,
            }
        return {
            "status": "ok",
            "list_name": normalized_list,
            "list_id": list_record.get("list_id"),
            "owner_user_id": owner,
            "input_list_name": list_name.strip().lower(),
            "matched_existing": matched_existing,
            "deleted": True,
            "deleted_item_count": item_count,
            "list_names": self._list_names(owner),
            "message": f"I deleted the `{normalized_list}` list.",
        }

    def remove_item(
        self,
        list_name: str,
        item_text: str,
        owner_user_id: str | None = None,
    ) -> dict[str, object]:
        owner = self._owner_user_id(owner_user_id)
        normalized_list, matched_existing = self._resolve_list_name(list_name, owner)
        if not normalized_list:
            return {
                "status": "needs_input",
                "message": "List name is required.",
                "missing_fields": ["list_name"],
            }
        exists = normalized_list in self._existing_list_names(owner)
        if not exists:
            return self._unknown_list_result(
                list_name=list_name,
                resolved_list_name=normalized_list,
                matched_existing=matched_existing,
                owner_user_id=owner,
            )

        normalized_item = item_text.strip()
        if not normalized_item:
            return {
                "status": "needs_input",
                "message": "Item text is required.",
                "missing_fields": ["item_text"],
            }

        if self._is_all_items_phrase(normalized_item):
            list_record = self._storage.get_list_record(owner_user_id=owner, list_name=normalized_list) or {}
            removed_count = self._storage.remove_all_items(
                owner_user_id=owner,
                list_name=normalized_list,
                timestamp=_utc_now(),
            )
            items = self._list_items(normalized_list, owner)
            return {
                "status": "ok",
                "list_name": normalized_list,
                "list_id": list_record.get("list_id"),
                "owner_user_id": owner,
                "input_list_name": list_name.strip().lower(),
                "matched_existing": matched_existing,
                "item_text": normalized_item,
                "removed_all": True,
                "removed_count": removed_count,
                "count": len(items),
                "items": items,
                "list_names": self._list_names(owner),
                "message": f"I removed all items from `{normalized_list}`.",
            }

        item_entries = self._list_item_entries(normalized_list, owner)
        matched_entry, suggestions, score = self._resolve_item_match(
            requested_item=normalized_item,
            item_entries=item_entries,
        )
        if matched_entry is None:
            return {
                "status": "unknown_item",
                "message": f"I could not find an item matching `{normalized_item}` in `{normalized_list}`.",
                "list_name": normalized_list,
                "input_item_text": normalized_item,
                "item_suggestions": suggestions,
                "suggested_item": suggestions[0] if suggestions else None,
                "suggestion_confidence": round(score, 3) if isinstance(score, float) else None,
                "available_items": [str(entry.get("item_name") or "") for entry in item_entries],
            }

        removed_item_name = str(matched_entry.get("item_name") or "").strip()
        removed_item_id = str(matched_entry.get("item_id") or "")
        removed = self._storage.remove_item_by_id(
            owner_user_id=owner,
            list_name=normalized_list,
            item_id=str(matched_entry.get("item_id") or ""),
            timestamp=_utc_now(),
        )
        if not removed:
            return {
                "status": "error",
                "message": f"I could not remove `{removed_item_name}` from `{normalized_list}`.",
                "list_name": normalized_list,
                "item_text": removed_item_name,
            }
        items = self._list_items(normalized_list, owner)
        return {
            "status": "ok",
            "list_name": normalized_list,
            "list_id": (self._storage.get_list_record(owner_user_id=owner, list_name=normalized_list) or {}).get("list_id"),
            "item_id": removed_item_id,
            "owner_user_id": owner,
            "input_list_name": list_name.strip().lower(),
            "matched_existing": matched_existing,
            "item_text": removed_item_name,
            "input_item_text": normalized_item,
            "removed": True,
            "count": len(items),
            "items": items,
            "list_names": self._list_names(owner),
            "message": f"Removed `{removed_item_name}` from `{normalized_list}`.",
        }

    def mark_item_done(
        self,
        *,
        list_name: str,
        item_text: str,
        completion_mode: str | None = None,
        owner_user_id: str | None = None,
    ) -> dict[str, object]:
        owner = self._owner_user_id(owner_user_id)
        normalized_list, matched_existing = self._resolve_list_name(list_name, owner)
        if not normalized_list:
            return {
                "status": "needs_input",
                "message": "List name is required.",
                "missing_fields": ["list_name"],
            }
        exists = normalized_list in self._existing_list_names(owner)
        if not exists:
            return self._unknown_list_result(
                list_name=list_name,
                resolved_list_name=normalized_list,
                matched_existing=matched_existing,
                owner_user_id=owner,
            )

        normalized_item = item_text.strip()
        if not normalized_item:
            return {
                "status": "needs_input",
                "message": "Item text is required.",
                "missing_fields": ["item_text"],
            }

        normalized_completion_mode = self._normalize_completion_mode(completion_mode)
        if normalized_completion_mode is None:
            return {
                "status": "needs_input",
                "message": (
                    f"Do you want me to remove `{normalized_item}` from `{normalized_list}`, "
                    "or mark it done?"
                ),
                "question": (
                    f"For `{normalized_item}`, should I `remove` it from `{normalized_list}` "
                    "or just mark it `done`?"
                ),
                "missing_fields": ["completion_mode"],
                "list_name": normalized_list,
                "item_text": normalized_item,
            }

        if normalized_completion_mode == "remove":
            removed = self.remove_item(
                list_name=normalized_list,
                item_text=normalized_item,
                owner_user_id=owner,
            )
            removed["completion_mode"] = "remove"
            return removed

        item_entries = self._list_item_entries(normalized_list, owner)
        matched_entry, suggestions, score = self._resolve_item_match(
            requested_item=normalized_item,
            item_entries=item_entries,
        )
        if matched_entry is None:
            return {
                "status": "unknown_item",
                "message": f"I could not find an item matching `{normalized_item}` in `{normalized_list}`.",
                "list_name": normalized_list,
                "input_item_text": normalized_item,
                "item_suggestions": suggestions,
                "suggested_item": suggestions[0] if suggestions else None,
                "suggestion_confidence": round(score, 3) if isinstance(score, float) else None,
                "available_items": [str(entry.get("item_name") or "") for entry in item_entries],
            }

        item_id = str(matched_entry.get("item_id") or "")
        item_name = str(matched_entry.get("item_name") or "").strip()
        updated = self._storage.set_item_checked(
            owner_user_id=owner,
            list_name=normalized_list,
            item_id=item_id,
            checked=True,
            timestamp=_utc_now(),
        )
        if not updated:
            return {
                "status": "error",
                "message": f"I could not mark `{item_name}` as done in `{normalized_list}`.",
                "list_name": normalized_list,
                "item_text": item_name,
            }

        entries_after = self._list_item_entries(normalized_list, owner)
        items = [str(entry.get("item_name") or "") for entry in entries_after if str(entry.get("item_name") or "")]
        checked_count = sum(1 for entry in entries_after if bool(entry.get("checked")))
        return {
            "status": "ok",
            "list_name": normalized_list,
            "list_id": (self._storage.get_list_record(owner_user_id=owner, list_name=normalized_list) or {}).get("list_id"),
            "item_id": item_id,
            "owner_user_id": owner,
            "input_list_name": list_name.strip().lower(),
            "matched_existing": matched_existing,
            "item_text": item_name,
            "input_item_text": normalized_item,
            "completion_mode": "done",
            "checked": True,
            "checked_count": checked_count,
            "count": len(items),
            "items": items,
            "item_entries": entries_after,
            "list_names": self._list_names(owner),
            "message": f"Marked `{item_name}` as done in `{normalized_list}`.",
        }

    def reset(self) -> None:
        self._storage.clear()

    def source_snapshot(
        self,
        *,
        list_name: str,
        owner_user_id: str | None = None,
    ) -> dict[str, object]:
        owner = self._owner_user_id(owner_user_id)
        normalized_list, _ = self._resolve_list_name(list_name, owner)
        record = self._storage.get_list_record(owner_user_id=owner, list_name=normalized_list)
        if record is None:
            return {
                "exists": False,
                "owner_user_id": owner,
                "list_name": normalized_list,
                "list_id": None,
                "items": [],
                "item_entries": [],
                "source_revision": None,
            }
        entries = self._list_item_entries(normalized_list, owner)
        return {
            "exists": True,
            "owner_user_id": owner,
            "list_name": normalized_list,
            "list_id": record.get("list_id"),
            "items": [str(item.get("item_name") or "") for item in entries],
            "item_entries": entries,
            "source_revision": record.get("updated_at"),
        }
