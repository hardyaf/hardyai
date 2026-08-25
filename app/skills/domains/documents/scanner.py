from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.skills.domains.documents.ingestion import (
    DocumentValidationError,
    TransientDocumentSpool,
    sanitize_filename,
)
from app.skills.domains.documents.service import DocumentIngestionService


class WatchedDocumentScanner:
    """Bounded, root-only scanner that claims stable files before staging them."""

    def __init__(
        self,
        *,
        root: str,
        spool: TransientDocumentSpool,
        ingestion: DocumentIngestionService,
        owner_id: str,
        stable_seconds: float = 5.0,
        max_files_per_scan: int = 20,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise ValueError("watched document root cannot be a symlink")
        os.chmod(self.root, 0o700)
        self.claims = self.root / ".claims"
        self.rejected = self.root / ".rejected"
        for path in (self.claims, self.rejected):
            path.mkdir(exist_ok=True)
            if path.is_symlink():
                raise ValueError("watched document control directories cannot be symlinks")
            os.chmod(path, 0o700)
        self.spool = spool
        self.ingestion = ingestion
        self.owner_id = str(owner_id or "").strip()
        if not self.owner_id:
            raise ValueError("watched document owner is required")
        self.stable_seconds = max(0.5, min(float(stable_seconds), 300.0))
        self.max_files_per_scan = max(1, min(int(max_files_per_scan), 100))
        self._observed: dict[str, tuple[int, int, float]] = {}

    def scan_once(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        results: list[dict[str, Any]] = []
        candidates: list[Path] = []
        for path in sorted(self.root.iterdir(), key=lambda item: item.name.casefold()):
            if len(candidates) >= self.max_files_per_scan:
                break
            if path.name.startswith(".") or path.is_symlink() or not path.is_file():
                continue
            candidates.append(path)
        live = {str(path) for path in candidates}
        self._observed = {key: value for key, value in self._observed.items() if key in live}
        for path in candidates:
            try:
                filename = sanitize_filename(path.name)
                stat_result = path.stat(follow_symlinks=False)
                signature = (int(stat_result.st_size), int(stat_result.st_mtime_ns))
                previous = self._observed.get(str(path))
                if previous is None or previous[:2] != signature:
                    self._observed[str(path)] = (*signature, now)
                    continue
                if now - previous[2] < self.stable_seconds:
                    continue
                claim = self.claims / f"{uuid4()}-{filename}"
                os.replace(path, claim)
                self._observed.pop(str(path), None)
                results.append(self._stage_claim(claim=claim, filename=filename))
            except (DocumentValidationError, OSError, RuntimeError, ValueError) as exc:
                results.append(
                    {
                        "status": "rejected",
                        "filename": path.name[:180],
                        "error_code": str(getattr(exc, "code", "") or type(exc).__name__)[:120],
                    }
                )
        return results

    def _stage_claim(self, *, claim: Path, filename: str) -> dict[str, Any]:
        writer = self.spool.begin(filename=filename, declared_media_type=None, title=None)
        try:
            with claim.open("rb") as stream:
                while True:
                    chunk = stream.read(65536)
                    if not chunk:
                        break
                    writer.write(chunk)
            staged = replace(writer.finish(), ingest_route="scanner")
            accepted = self.ingestion.accept(owner_id=self.owner_id, staged=staged)
        except Exception:
            writer.abort()
            destination = self.rejected / claim.name
            if claim.exists():
                os.replace(claim, destination)
            raise
        claim.unlink(missing_ok=True)
        return {
            "status": "queued" if accepted.enqueue_confirmed else "awaiting_enqueue",
            "document_id": accepted.record.document_id,
            "created": accepted.created,
            "filename": filename,
        }
