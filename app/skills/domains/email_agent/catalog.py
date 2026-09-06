from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from app.skills.domains.email_agent.config import EmailAgentPermissions


def _opaque_ref(prefix: str, value: str) -> str:
    digest = hashlib.sha256(f"email-catalog-v1\n{prefix}\n{value}".encode("utf-8")).hexdigest()
    return f"{prefix}_v1_{digest[:24]}"


def _selector(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


@dataclass(frozen=True, slots=True)
class CatalogResolution:
    status: str
    canonical_refs: tuple[str, ...] = ()
    candidates: tuple[dict[str, str], ...] = ()


class EmailCatalogService:
    """Safe, request-agnostic catalogs for routed views and additive labels."""

    def __init__(self, *, permissions: EmailAgentPermissions, storage: Any) -> None:
        self._permissions = permissions
        self._storage = storage

    @staticmethod
    def mailbox_ref(route_key: str) -> str:
        return _opaque_ref("mailbox", str(route_key or "").strip().casefold())

    @staticmethod
    def label_ref(label_key: str) -> str:
        return _opaque_ref("label", str(label_key or "").strip().casefold())

    def mailboxes(self) -> list[dict[str, Any]]:
        stats = self._storage.mailbox_catalog_stats(
            allowed_source_keys=tuple(item.route_key for item in self._permissions.source_routes)
        )
        by_key = {str(item.get("route_key") or ""): item for item in stats}
        rows: list[dict[str, Any]] = []
        for route in self._permissions.source_routes:
            row = {
                "mailbox_ref": self.mailbox_ref(route.route_key),
                "display_name": route.display_name,
                "message_count": int(by_key.get(route.route_key, {}).get("message_count", 0)),
            }
            earliest = by_key.get(route.route_key, {}).get("earliest_indexed_at")
            latest = by_key.get(route.route_key, {}).get("latest_indexed_at")
            if earliest:
                row["earliest_indexed_at"] = earliest
            if latest:
                row["latest_indexed_at"] = latest
            rows.append(row)
        return rows

    def labels(self, *, text: str | None = None) -> list[dict[str, Any]]:
        needle = _selector(text)
        rows = []
        for label in self._permissions.managed_labels:
            if not label.enabled:
                continue
            if needle and needle not in label.display_name.casefold() and needle != label.key:
                continue
            rows.append(
                {
                    "label_ref": self.label_ref(label.key),
                    "display_name": label.display_name,
                }
            )
        return rows

    def resolve_mailboxes(self, values: Iterable[Any]) -> CatalogResolution:
        catalog = self.mailboxes()
        aliases: dict[str, list[str]] = {}
        for route, row in zip(self._permissions.source_routes, catalog, strict=True):
            for alias in {
                row["mailbox_ref"].casefold(),
                route.route_key.casefold(),
                route.route_key.replace("_", " ").casefold(),
                route.display_name.casefold(),
            }:
                aliases.setdefault(alias, []).append(str(row["mailbox_ref"]))
        return self._resolve(values, aliases=aliases, catalog=catalog, ref_field="mailbox_ref")

    def resolve_labels(self, values: Iterable[Any]) -> CatalogResolution:
        catalog = self.labels()
        by_key = {item.key: item for item in self._permissions.managed_labels if item.enabled}
        aliases: dict[str, list[str]] = {}
        for row in catalog:
            label = next(item for item in by_key.values() if self.label_ref(item.key) == row["label_ref"])
            for alias in {
                row["label_ref"].casefold(),
                label.key.casefold(),
                label.key.replace("_", " ").casefold(),
                label.display_name.casefold(),
            }:
                aliases.setdefault(alias, []).append(str(row["label_ref"]))
        return self._resolve(values, aliases=aliases, catalog=catalog, ref_field="label_ref")

    def route_keys_for_refs(self, refs: Iterable[str]) -> tuple[str, ...]:
        by_ref = {
            self.mailbox_ref(route.route_key): route.route_key
            for route in self._permissions.source_routes
        }
        return tuple(by_ref[str(ref)] for ref in refs)

    def label_keys_for_refs(self, refs: Iterable[str]) -> tuple[str, ...]:
        by_ref = {
            self.label_ref(label.key): label.key
            for label in self._permissions.managed_labels
            if label.enabled
        }
        return tuple(by_ref[str(ref)] for ref in refs)

    @staticmethod
    def _resolve(
        values: Iterable[Any],
        *,
        aliases: dict[str, list[str]],
        catalog: list[dict[str, Any]],
        ref_field: str,
    ) -> CatalogResolution:
        resolved: list[str] = []
        for raw in values:
            value = _selector(raw)
            matches = tuple(dict.fromkeys(aliases.get(value, ())))
            if len(matches) != 1:
                candidates = tuple(
                    {
                        ref_field: str(item[ref_field]),
                        "display_name": str(item["display_name"]),
                    }
                    for item in catalog[:10]
                )
                return CatalogResolution(
                    status="missing" if not matches else "ambiguous",
                    candidates=candidates,
                )
            if matches[0] in resolved:
                return CatalogResolution(status="duplicate")
            resolved.append(matches[0])
        return CatalogResolution(status="ok", canonical_refs=tuple(resolved))
