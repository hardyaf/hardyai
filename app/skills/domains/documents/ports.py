from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, BinaryIO, Protocol

from app.skills.domains.documents.types import DocumentArtifact


class DurableDocumentEnqueuePort(Protocol):
    def enqueue_document(self, *, document_id: str, intake_id: str, sha256: str) -> str:
        """Durably enqueue a content-free archive job and return its job ID."""

    def enqueue_processing(
        self,
        *,
        document_id: str,
        source_version_id: str,
        run_id: str,
    ) -> str:
        """Durably enqueue one immutable processing run and return its job ID."""


@dataclass(frozen=True)
class ArchiveTask:
    task_ref: str
    state: str
    source_external_id: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class ArchiveSearchHit:
    source_external_id: str
    title: str
    snippet: str


class ArchiveIngestPort(Protocol):
    def submit(self, *, stream: BinaryIO, filename: str, title: str) -> str:
        ...

    def task_status(self, task_ref: str) -> ArchiveTask:
        ...

    def grant_read_access(self, source_external_id: str) -> None:
        ...

    def download_original(self, source_external_id: str) -> Iterator[bytes]:
        ...


class ArchiveReadPort(Protocol):
    def search(self, *, query: str, limit: int) -> list[ArchiveSearchHit]:
        ...

    def download_original(self, source_external_id: str) -> Iterator[bytes]:
        ...


@dataclass(frozen=True)
class ArchiveOrigin:
    external_id: str
    external_version: str | None
    title: str
    original_filename: str
    media_type: str
    modified_at: str | None


class ArchiveDiscoveryPort(Protocol):
    def list_origins(self, *, limit: int) -> tuple[list[ArchiveOrigin], bool]:
        ...

    def download_original(self, source_external_id: str) -> Iterator[bytes]:
        ...


@dataclass(frozen=True)
class ParserSubmission:
    operation_ref: str


@dataclass(frozen=True)
class ParserOperation:
    operation_ref: str
    state: str
    error_code: str | None = None


class ParserOperationUnavailable(RuntimeError):
    """The provider no longer recognizes a previously durable operation reference."""


class DocumentParserPort(Protocol):
    provider_name: str
    provider_version: str

    def submit(
        self,
        *,
        stream: BinaryIO,
        filename: str,
        media_type: str,
    ) -> ParserSubmission:
        ...

    def status(self, operation_ref: str) -> ParserOperation:
        ...

    def result(
        self,
        *,
        operation_ref: str,
        document_id: str,
        source_version_id: str,
        run_id: str,
    ) -> DocumentArtifact:
        ...

    def ready(self) -> bool:
        ...


class DocumentQueryPort(Protocol):
    def ready(self) -> bool: ...

    def status(self, document_id: str) -> dict[str, Any]: ...

    def find(self, *, query: str, limit: int) -> dict[str, Any]: ...

    def evidence(
        self,
        *,
        document_id: str,
        block_id: str | None = None,
        page_number: int | None = None,
        limit: int = 10,
    ) -> dict[str, Any]: ...

    def reprocess(self, *, document_id: str, idempotency_key: str) -> dict[str, Any]: ...

    def propose_metadata(
        self,
        *,
        document_id: str,
        field_name: str,
        proposed_value: str,
    ) -> dict[str, Any]: ...

    def bind_metadata_review(
        self,
        *,
        document_id: str,
        proposal_id: str,
        review_id: str,
    ) -> None: ...

    def source_path(self, document_id: str) -> str: ...
