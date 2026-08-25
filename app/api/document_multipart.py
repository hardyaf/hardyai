from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.requests import ClientDisconnect

from app.skills.domains.documents.ingestion import (
    DocumentValidationError,
    StagingWriter,
    TransientDocumentSpool,
)
from app.skills.domains.documents.types import StagedDocument


class _MultipartState:
    def __init__(self, spool: TransientDocumentSpool) -> None:
        self.spool = spool
        self.header_name = bytearray()
        self.header_value = bytearray()
        self.headers: dict[bytes, bytes] = {}
        self.part_name = ""
        self.part_buffer = bytearray()
        self.writer: StagingWriter | None = None
        self.file_seen = False
        self.title = ""
        self.part_count = 0

    def callbacks(self) -> dict[str, Any]:
        return {
            "on_part_begin": self.on_part_begin,
            "on_part_data": self.on_part_data,
            "on_part_end": self.on_part_end,
            "on_header_field": self.on_header_field,
            "on_header_value": self.on_header_value,
            "on_header_end": self.on_header_end,
            "on_headers_finished": self.on_headers_finished,
        }

    def on_part_begin(self) -> None:
        self.part_count += 1
        if self.part_count > 2:
            raise DocumentValidationError("too_many_multipart_parts")
        self.headers = {}
        self.part_name = ""
        self.part_buffer = bytearray()

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        if len(self.header_name) + end - start > 128:
            raise DocumentValidationError("multipart_header_too_large")
        self.header_name.extend(data[start:end])

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        if len(self.header_value) + end - start > 1024:
            raise DocumentValidationError("multipart_header_too_large")
        self.header_value.extend(data[start:end])

    def on_header_end(self) -> None:
        self.headers[bytes(self.header_name).strip().lower()] = bytes(self.header_value).strip()
        self.header_name.clear()
        self.header_value.clear()

    def on_headers_finished(self) -> None:
        disposition = self.headers.get(b"content-disposition", b"")
        kind, options = parse_options_header(disposition)
        if kind != b"form-data":
            raise DocumentValidationError("invalid_multipart_disposition")
        self.part_name = options.get(b"name", b"").decode("utf-8", errors="strict")
        if self.part_name == "document":
            if self.file_seen or self.writer is not None:
                raise DocumentValidationError("multiple_documents_not_supported")
            filename = options.get(b"filename", b"").decode("utf-8", errors="strict")
            if not filename:
                raise DocumentValidationError("missing_filename")
            media_type = self.headers.get(b"content-type", b"").decode("ascii", errors="ignore")
            self.writer = self.spool.begin(
                filename=filename,
                declared_media_type=media_type,
                title=None,
            )
            self.file_seen = True
        elif self.part_name != "title":
            raise DocumentValidationError("unsupported_multipart_field")

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        chunk = data[start:end]
        if self.part_name == "document":
            if self.writer is None:
                raise DocumentValidationError("document_part_not_initialized")
            self.writer.write(chunk)
        elif self.part_name == "title":
            if len(self.part_buffer) + len(chunk) > 800:
                raise DocumentValidationError("title_too_large")
            self.part_buffer.extend(chunk)

    def on_part_end(self) -> None:
        if self.part_name == "title":
            self.title = self.part_buffer.decode("utf-8", errors="strict").strip()[:200]

    def finish(self) -> StagedDocument:
        if self.writer is None or not self.file_seen:
            raise DocumentValidationError("missing_document")
        return self.writer.finish(title=self.title or None)

    def abort(self) -> None:
        if self.writer is not None:
            self.writer.abort()


async def stream_document_multipart(request: Request, spool: TransientDocumentSpool) -> StagedDocument:
    content_type = str(request.headers.get("content-type") or "")
    kind, options = parse_options_header(content_type.encode("latin-1"))
    boundary = options.get(b"boundary")
    if kind != b"multipart/form-data" or not boundary or len(boundary) > 200:
        raise HTTPException(status_code=415, detail="multipart_form_data_required")
    state = _MultipartState(spool)
    parser = MultipartParser(boundary, state.callbacks())
    try:
        async for chunk in request.stream():
            parser.write(chunk)
        parser.finalize()
        return state.finish()
    except DocumentValidationError as exc:
        state.abort()
        if exc.code == "document_too_large":
            status_code = 413
        elif exc.code in {"spool_quota_exceeded", "spool_free_space_floor"}:
            status_code = 507
        else:
            status_code = 400
        raise HTTPException(status_code=status_code, detail=exc.code) from exc
    except (UnicodeError, ValueError) as exc:
        state.abort()
        raise HTTPException(status_code=400, detail="invalid_multipart_body") from exc
    except ClientDisconnect as exc:
        state.abort()
        raise HTTPException(status_code=400, detail="upload_disconnected") from exc
    except Exception:
        state.abort()
        raise
