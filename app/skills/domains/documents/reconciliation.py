from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from app.skills.domains.documents.ingestion import sanitize_filename, sanitize_title
from app.skills.domains.documents.ports import ArchiveDiscoveryPort
from app.skills.domains.documents.storage import DocumentRepository, DocumentStorageError


class DocumentOriginReconciler:
    def __init__(
        self,
        *,
        repository: DocumentRepository,
        provider: ArchiveDiscoveryPort,
        owner_id: str,
        max_source_bytes: int,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.owner_id = str(owner_id or "").strip()
        self.max_source_bytes = max(1024, int(max_source_bytes))
        if not self.owner_id:
            raise ValueError("Paperless origin owner is required")

    def reconcile(self, *, limit: int = 50) -> dict[str, int | str]:
        observed = datetime.now(UTC)
        latest_text = self.repository.latest_provider_observation(provider="paperless")
        if latest_text:
            try:
                latest = datetime.fromisoformat(latest_text)
            except ValueError:
                latest = None
            if latest is not None and observed <= latest:
                observed = latest + timedelta(microseconds=1)
        observed_at = observed.isoformat()
        origins, complete = self.provider.list_origins(limit=max(1, min(int(limit), 100)))
        created = 0
        updated = 0
        conflicts = 0
        for origin in origins:
            try:
                filename = sanitize_filename(origin.original_filename)
                if origin.media_type not in {"application/pdf", "image/jpeg", "image/png"}:
                    raise ValueError("unsupported origin type")
                digest = hashlib.sha256()
                size = 0
                for chunk in self.provider.download_original(origin.external_id):
                    size += len(chunk)
                    if size > self.max_source_bytes:
                        raise ValueError("origin source too large")
                    digest.update(chunk)
                if not size:
                    raise ValueError("origin source empty")
                _, was_created = self.repository.reconcile_discovered_origin(
                    provider="paperless",
                    external_id=origin.external_id,
                    external_version=origin.external_version,
                    owner_id=self.owner_id,
                    title=sanitize_title(origin.title, fallback=filename.rsplit(".", 1)[0]),
                    original_filename=filename,
                    media_type=origin.media_type,
                    sha256=digest.hexdigest(),
                    size_bytes=size,
                    observed_at=observed_at,
                )
                created += int(was_created)
                updated += int(not was_created)
            except (DocumentStorageError, ValueError):
                conflicts += 1
        missing = (
            self.repository.mark_missing_provider_origins(
                provider="paperless",
                observed_at=observed_at,
            )
            if complete
            else 0
        )
        return {
            "status": "complete" if complete else "bounded_partial",
            "observed": len(origins),
            "created": created,
            "updated": updated,
            "conflicts": conflicts,
            "missing": missing,
        }
