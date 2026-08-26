from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import replace
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.document_multipart import stream_document_multipart
from app.api.operator_auth import require_operator
from app.api.principals import RequestPrincipal
from app.composition.documents import DocumentGatewayContainer
from app.schemas.documents import (
    DocumentSearchResponse,
    DocumentSearchResult,
    DocumentEvidenceBlock,
    DocumentEvidenceResponse,
    DocumentReprocessRequest,
    DocumentReprocessResponse,
    DocumentMetadataProposalRequest,
    DocumentMetadataProposalResponse,
    DocumentMetadataReviewBindingRequest,
    DocumentStatusResponse,
    DocumentUploadResponse,
    DocumentClassificationsResponse,
    DocumentFieldsResponse,
    DocumentActionExecutionBindingRequest,
    DocumentActionProposalView,
    DocumentProposalsResponse,
    DocumentStructuredSearchResponse,
    DocumentIntelligenceResponse,
    RestrictedDocumentAccessRequest,
)
from app.restricted_documents.readiness import evaluate_restricted_workflow
from app.skills.domains.documents.types import DocumentRecord
from app.skills.domains.documents.permissions import DocumentAccessPolicy
from app.skills.domains.documents.storage import DocumentStorageError


router = APIRouter(prefix="/documents", tags=["documents"])


def _container(request: Request) -> DocumentGatewayContainer:
    value = request.app.state.document_container
    if not isinstance(value, DocumentGatewayContainer):
        raise RuntimeError("document gateway container is not configured")
    return value


def _enabled(container: DocumentGatewayContainer) -> None:
    if not container.enabled or container.repository is None:
        raise HTTPException(status_code=503, detail="documents_disabled")


def _restricted_readiness(container: DocumentGatewayContainer):
    settings = container.settings
    return evaluate_restricted_workflow(
        enabled=settings.documents_restricted_workflow_enabled,
        cipher_configured=False,
        isolated_store_configured=False,
        security_review_id=settings.documents_restricted_security_review_id,
        recovery_attestation_path=settings.documents_restricted_recovery_attestation_path,
    )


def _status(record: DocumentRecord) -> DocumentStatusResponse:
    return DocumentStatusResponse(
        document_id=record.document_id,
        intake_id=record.intake_id,
        title=record.title,
        media_type=record.media_type,
        size_bytes=record.size_bytes,
        sha256=record.sha256,
        state=record.state.value,
        source_available=record.source_ref is not None,
        created_at=record.created_at,
        updated_at=record.updated_at,
        failure_code=record.failure_code,
        sensitivity=record.sensitivity.value,
        processing_state=record.processing_state.value,
        source_version_id=record.source_version_id,
        active_run_id=record.active_run_id,
        document_class=record.document_class.value if record.document_class is not None else None,
        classification_state=record.classification_state,
        archive_text_visible=record.archive_text_visible,
    )


def _document_id(value: str) -> str:
    try:
        return str(UUID(str(value or "")))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="document_not_found") from exc


def _proposal_id(value: str) -> str:
    try:
        return str(UUID(str(value or "")))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="document_action_proposal_not_found") from exc


def _ingress_key(source: str | None, external_id: str | None) -> tuple[str, str] | None:
    normalized_source = str(source or "").strip().casefold()
    normalized_external_id = str(external_id or "").strip().casefold()
    if not normalized_source and not normalized_external_id:
        return None
    if not normalized_source or not normalized_external_id:
        raise HTTPException(status_code=400, detail="incomplete_ingress_identity")
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,39}", normalized_source):
        raise HTTPException(status_code=400, detail="invalid_ingress_source")
    if not re.fullmatch(r"[0-9a-f]{64}", normalized_external_id):
        raise HTTPException(status_code=400, detail="invalid_ingress_external_id")
    return normalized_source, normalized_external_id


def _upload_payload(record, *, duplicate: bool) -> DocumentUploadResponse:
    return DocumentUploadResponse(
        **_status(record).model_dump(),
        duplicate=duplicate,
        enqueue_confirmed=record.durable_job_id is not None,
    )


def _verified_source(chunks: Iterable[bytes], *, expected_size: int, expected_sha256: str) -> Iterator[bytes]:
    digest = hashlib.sha256()
    observed_size = 0
    for chunk in chunks:
        observed_size += len(chunk)
        if observed_size > expected_size:
            raise RuntimeError("document_source_integrity_failure")
        digest.update(chunk)
        yield chunk
    if observed_size != expected_size or digest.hexdigest() != expected_sha256:
        raise RuntimeError("document_source_integrity_failure")


@router.get("/ready")
async def documents_ready(request: Request) -> JSONResponse:
    status = await asyncio.to_thread(_container(request).readiness)
    return JSONResponse(status_code=200 if status["status"] in {"ready", "disabled"} else 503, content=status)


@router.get("/restricted/ready")
async def restricted_workflow_readiness(
    request: Request,
    _: RequestPrincipal = Depends(require_operator),
) -> JSONResponse:
    readiness = _restricted_readiness(_container(request))
    return JSONResponse(
        content=readiness.public_view(),
        status_code=200 if readiness.ready else 503,
        headers={"Cache-Control": "no-store"},
    )


@router.post("", response_model=DocumentUploadResponse)
async def upload_document(
    request: Request,
    principal: RequestPrincipal = Depends(require_operator),
    x_jarvis_ingress_source: str | None = Header(default=None),
    x_jarvis_ingress_external_id: str | None = Header(default=None),
) -> JSONResponse:
    container = _container(request)
    _enabled(container)
    if container.spool is None or container.ingestion is None:
        raise HTTPException(status_code=503, detail="document_ingestion_unavailable")
    ingress_key = _ingress_key(x_jarvis_ingress_source, x_jarvis_ingress_external_id)
    if ingress_key is not None and container.repository is not None:
        existing = await asyncio.to_thread(
            container.repository.get_for_ingress,
            ingress_source=ingress_key[0],
            external_id=ingress_key[1],
            owner_id=principal.user_id,
        )
        if existing is not None:
            payload = _upload_payload(existing, duplicate=True)
            return JSONResponse(status_code=200, content=payload.model_dump())
    staged = await stream_document_multipart(request, container.spool)
    if ingress_key is not None:
        staged = replace(staged, ingest_route=ingress_key[0])
    result = await asyncio.to_thread(container.ingestion.accept, owner_id=principal.user_id, staged=staged)
    record = result.record
    if ingress_key is not None and container.repository is not None:
        try:
            record = await asyncio.to_thread(
                container.repository.bind_ingress_receipt,
                ingress_source=ingress_key[0],
                external_id=ingress_key[1],
                owner_id=principal.user_id,
                document_id=record.document_id,
            )
        except DocumentStorageError as exc:
            raise HTTPException(status_code=409, detail=exc.code) from exc
    payload = _upload_payload(record, duplicate=not result.created)
    status_code = 200 if not result.created else 202
    return JSONResponse(status_code=status_code, content=payload.model_dump())


@router.get(
    "/ingress-receipts/{ingress_source}/{external_id}",
    response_model=DocumentUploadResponse,
)
async def get_ingress_receipt(
    request: Request,
    ingress_source: str,
    external_id: str,
    principal: RequestPrincipal = Depends(require_operator),
) -> DocumentUploadResponse:
    container = _container(request)
    _enabled(container)
    if container.repository is None:
        raise HTTPException(status_code=503, detail="document_ingestion_unavailable")
    key = _ingress_key(ingress_source, external_id)
    assert key is not None
    record = await asyncio.to_thread(
        container.repository.get_for_ingress,
        ingress_source=key[0],
        external_id=key[1],
        owner_id=principal.user_id,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="document_ingress_receipt_not_found")
    return _upload_payload(record, duplicate=True)


@router.get("/search", response_model=DocumentSearchResponse)
async def search_documents(
    request: Request,
    query: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=10, ge=1, le=20),
    principal: RequestPrincipal = Depends(require_operator),
) -> DocumentSearchResponse:
    container = _container(request)
    _enabled(container)
    if container.archive_reader is None or container.repository is None:
        raise HTTPException(status_code=503, detail="document_search_unavailable")
    local_hits = await asyncio.to_thread(
        container.repository.search_blocks,
        owner_id=principal.user_id,
        query=query,
        limit=limit,
    )
    results: list[DocumentSearchResult] = []
    seen: set[str] = set()
    for hit in local_hits:
        document_id = str(hit["document_id"])
        seen.add(document_id)
        results.append(
            DocumentSearchResult(
                document_id=document_id,
                title=str(hit["title"]),
                snippet=str(hit["literal_text"])[:500],
                state="ready",
                processing_state="complete",
                page_number=int(hit["page_number"]),
                block_id=str(hit["block_id"]),
                evidence_path=f"/documents/{document_id}/evidence?block_id={quote(str(hit['block_id']), safe='')}",
            )
        )
    hits = await asyncio.to_thread(
        container.archive_reader.search,
        query=query,
        limit=limit,
    )
    for hit in hits:
        record = container.repository.document_for_external_id(
            provider=container.archive_reader.provider_name,
            external_id=hit.source_external_id,
        )
        if (
            record is None
            or record.document_id in seen
            or not DocumentAccessPolicy.can_read_archive_text(
                record=record,
                user_id=principal.user_id,
            )
        ):
            continue
        results.append(
            DocumentSearchResult(
                document_id=record.document_id,
                title=hit.title or record.title,
                snippet=hit.snippet,
                state=record.state.value,
                processing_state=record.processing_state.value,
            )
        )
    return DocumentSearchResponse(query=" ".join(query.split()), results=results[:limit])


@router.get("/structured-search", response_model=DocumentStructuredSearchResponse)
async def structured_search_documents(
    request: Request,
    amount: str | None = Query(default=None, max_length=40),
    correspondent: str | None = Query(default=None, max_length=120),
    period: str | None = Query(default=None, max_length=40),
    date_value: str | None = Query(default=None, alias="date", max_length=40),
    project: str | None = Query(default=None, max_length=120),
    clause: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=20, ge=1, le=100),
    principal: RequestPrincipal = Depends(require_operator),
) -> DocumentStructuredSearchResponse:
    container = _container(request)
    _enabled(container)
    rows = await asyncio.to_thread(
        container.repository.structured_search,
        owner_id=principal.user_id,
        amount=amount,
        correspondent=correspondent,
        period=period,
        date_value=date_value,
        project=project,
        clause=clause,
        limit=limit,
    )
    return DocumentStructuredSearchResponse(results=rows)


@router.post("/{document_id}/reprocess", response_model=DocumentReprocessResponse)
async def reprocess_document(
    document_id: str,
    body: DocumentReprocessRequest,
    request: Request,
    principal: RequestPrincipal = Depends(require_operator),
) -> JSONResponse:
    container = _container(request)
    _enabled(container)
    if container.reprocessing is None:
        raise HTTPException(status_code=503, detail="document_processing_unavailable")
    try:
        result = await asyncio.to_thread(
            container.reprocessing.request,
            document_id=_document_id(document_id),
            owner_id=principal.user_id,
            idempotency_key=body.idempotency_key,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="document_not_found") from exc
    except Exception as exc:
        code = str(getattr(exc, "code", "") or "document_reprocess_rejected")
        raise HTTPException(status_code=409, detail=code) from exc
    payload = DocumentReprocessResponse(**result)
    return JSONResponse(status_code=202, content=payload.model_dump())


@router.get("/{document_id}/evidence", response_model=DocumentEvidenceResponse)
async def get_document_evidence(
    document_id: str,
    request: Request,
    block_id: str | None = Query(default=None, min_length=1, max_length=120),
    page_number: int | None = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    principal: RequestPrincipal = Depends(require_operator),
) -> DocumentEvidenceResponse:
    container = _container(request)
    _enabled(container)
    if container.repository is None:
        raise HTTPException(status_code=503, detail="document_evidence_unavailable")
    canonical_id = _document_id(document_id)
    record = container.repository.get(canonical_id, owner_id=principal.user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    rows = await asyncio.to_thread(
        container.repository.evidence_blocks,
        document_id=canonical_id,
        owner_id=principal.user_id,
        block_id=block_id,
        page_number=page_number,
        limit=limit,
    )
    blocks: list[DocumentEvidenceBlock] = []
    for row in rows:
        blocks.append(
            DocumentEvidenceBlock(
                run_id=str(row["run_id"]),
                block_id=str(row["block_id"]),
                page_number=int(row["page_number"]),
                block_kind=str(row["block_kind"]),
                reading_order=int(row["reading_order"]),
                literal_text=str(row["literal_text"]),
                bbox=json.loads(row["bbox_json"]) if row["bbox_json"] else None,
                char_span=json.loads(row["char_span_json"]) if row["char_span_json"] else None,
                provider_ref=str(row["provider_ref"]) if row["provider_ref"] else None,
            )
        )
    return DocumentEvidenceResponse(
        document_id=canonical_id,
        title=record.title,
        sensitivity=record.sensitivity.value,
        source_path=f"/documents/{canonical_id}/source",
        blocks=blocks,
    )


@router.post(
    "/{document_id}/metadata-proposals",
    response_model=DocumentMetadataProposalResponse,
)
async def create_document_metadata_proposal(
    document_id: str,
    body: DocumentMetadataProposalRequest,
    request: Request,
    principal: RequestPrincipal = Depends(require_operator),
) -> JSONResponse:
    container = _container(request)
    _enabled(container)
    if container.repository is None:
        raise HTTPException(status_code=503, detail="document_metadata_unavailable")
    canonical_id = _document_id(document_id)
    record = container.repository.get(canonical_id, owner_id=principal.user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    try:
        proposal = await asyncio.to_thread(
            container.repository.create_metadata_proposal,
            document_id=canonical_id,
            field_name=body.field_name,
            proposed_value=body.proposed_value,
            sensitivity=record.sensitivity,
        )
    except (ValueError, RuntimeError) as exc:
        code = str(getattr(exc, "code", "") or "metadata_proposal_rejected")
        raise HTTPException(status_code=409, detail=code) from exc
    payload = DocumentMetadataProposalResponse(**proposal)
    return JSONResponse(status_code=202, content=payload.model_dump())


@router.post(
    "/{document_id}/metadata-proposals/{proposal_id}/review-binding",
    status_code=204,
)
async def bind_document_metadata_review(
    document_id: str,
    proposal_id: str,
    body: DocumentMetadataReviewBindingRequest,
    request: Request,
    principal: RequestPrincipal = Depends(require_operator),
) -> None:
    container = _container(request)
    _enabled(container)
    if container.repository is None:
        raise HTTPException(status_code=503, detail="document_metadata_unavailable")
    canonical_id = _document_id(document_id)
    record = container.repository.get(canonical_id, owner_id=principal.user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    bound = await asyncio.to_thread(
        container.repository.bind_metadata_review,
        document_id=canonical_id,
        proposal_id=proposal_id,
        review_id=body.review_id,
    )
    if not bound:
        raise HTTPException(status_code=404, detail="metadata_proposal_not_found")


@router.get("/{document_id}", response_model=DocumentStatusResponse)
async def get_document_status(
    document_id: str,
    request: Request,
    principal: RequestPrincipal = Depends(require_operator),
) -> DocumentStatusResponse:
    container = _container(request)
    _enabled(container)
    canonical_id = _document_id(document_id)
    record = container.repository.get(canonical_id, owner_id=principal.user_id) if container.repository else None
    if record is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    return _status(record)


@router.post("/{document_id}/restricted-fields")
async def read_restricted_document_field(
    document_id: str,
    body: RestrictedDocumentAccessRequest,
    request: Request,
    principal: RequestPrincipal = Depends(require_operator),
) -> JSONResponse:
    container = _container(request)
    _enabled(container)
    canonical_id = _document_id(document_id)
    record = container.repository.get(canonical_id, owner_id=principal.user_id) if container.repository else None
    if record is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    readiness = _restricted_readiness(container)
    reason = (
        "document_not_restricted"
        if record.sensitivity.value not in {"identity", "highly_restricted"}
        else "restricted_workflow_not_ready"
    )
    await asyncio.to_thread(
        container.repository.record_restricted_access,
        document_id=canonical_id,
        actor_principal=principal.subject,
        purpose_code=body.purpose,
        operation="restricted.read",
        outcome="denied",
        reason_code=reason,
        request_id=body.request_id,
    )
    return JSONResponse(
        status_code=503 if not readiness.ready else 409,
        content={
            "status": "blocked",
            "reason": reason,
            "readiness": readiness.public_view(),
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.get("/{document_id}/restricted-access-audit")
async def get_restricted_access_audit(
    document_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    principal: RequestPrincipal = Depends(require_operator),
) -> JSONResponse:
    container = _container(request)
    _enabled(container)
    canonical_id = _document_id(document_id)
    record = container.repository.get(canonical_id, owner_id=principal.user_id) if container.repository else None
    if record is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    rows = await asyncio.to_thread(
        container.repository.list_restricted_access_audit,
        document_id=canonical_id,
        limit=limit,
    )
    return JSONResponse(
        content={"document_id": canonical_id, "audit": rows},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.get(
    "/{document_id}/classifications",
    response_model=DocumentClassificationsResponse,
)
async def get_document_classifications(
    document_id: str,
    request: Request,
    principal: RequestPrincipal = Depends(require_operator),
) -> DocumentClassificationsResponse:
    container = _container(request)
    _enabled(container)
    canonical_id = _document_id(document_id)
    record = container.repository.get(canonical_id, owner_id=principal.user_id) if container.repository else None
    if record is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    rows = container.repository.list_classifications(document_id=canonical_id)
    for row in rows:
        row["selected"] = bool(row["selected"])
    return DocumentClassificationsResponse(
        document_id=canonical_id,
        classification_state=record.classification_state,
        classifications=rows,
    )


@router.get("/{document_id}/fields", response_model=DocumentFieldsResponse)
async def get_document_fields(
    document_id: str,
    request: Request,
    principal: RequestPrincipal = Depends(require_operator),
) -> DocumentFieldsResponse:
    container = _container(request)
    _enabled(container)
    canonical_id = _document_id(document_id)
    record = container.repository.get(canonical_id, owner_id=principal.user_id) if container.repository else None
    if record is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    if not DocumentAccessPolicy.can_read_fields(record=record, user_id=principal.user_id):
        raise HTTPException(status_code=403, detail="protected_fields_unavailable")
    return DocumentFieldsResponse(
        document_id=canonical_id,
        source_version_id=str(record.source_version_id or ""),
        fields=container.repository.effective_fields(document_id=canonical_id),
    )


@router.get("/{document_id}/proposals", response_model=DocumentProposalsResponse)
async def get_document_proposals(
    document_id: str,
    request: Request,
    principal: RequestPrincipal = Depends(require_operator),
) -> DocumentProposalsResponse:
    container = _container(request)
    _enabled(container)
    canonical_id = _document_id(document_id)
    record = container.repository.get(canonical_id, owner_id=principal.user_id) if container.repository else None
    if record is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    if not DocumentAccessPolicy.can_read_fields(record=record, user_id=principal.user_id):
        raise HTTPException(status_code=403, detail="protected_proposals_unavailable")
    proposals = container.repository.list_document_proposals(document_id=canonical_id)
    return DocumentProposalsResponse(document_id=canonical_id, **proposals)


@router.get("/{document_id}/intelligence", response_model=DocumentIntelligenceResponse)
async def get_document_intelligence(
    document_id: str,
    request: Request,
    principal: RequestPrincipal = Depends(require_operator),
) -> DocumentIntelligenceResponse:
    container = _container(request)
    _enabled(container)
    canonical_id = _document_id(document_id)
    record = container.repository.get(canonical_id, owner_id=principal.user_id) if container.repository else None
    if record is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    if not DocumentAccessPolicy.can_read_fields(record=record, user_id=principal.user_id):
        raise HTTPException(status_code=403, detail="protected_intelligence_unavailable")
    return DocumentIntelligenceResponse(
        document_id=canonical_id,
        **container.repository.list_intelligence(document_id=canonical_id),
    )


@router.get(
    "/action-proposals/{proposal_id}",
    response_model=DocumentActionProposalView,
)
async def get_document_action_proposal(
    proposal_id: str,
    request: Request,
    principal: RequestPrincipal = Depends(require_operator),
) -> DocumentActionProposalView:
    container = _container(request)
    _enabled(container)
    proposal = (
        container.repository.get_action_proposal(proposal_id=_proposal_id(proposal_id))
        if container.repository
        else None
    )
    if proposal is None or str(proposal.get("owner_id")) != principal.user_id:
        raise HTTPException(status_code=404, detail="document_action_proposal_not_found")
    return DocumentActionProposalView(**proposal)


@router.post(
    "/action-proposals/{proposal_id}/execution-binding",
    response_model=DocumentActionProposalView,
)
async def bind_document_action_execution(
    proposal_id: str,
    body: DocumentActionExecutionBindingRequest,
    request: Request,
    principal: RequestPrincipal = Depends(require_operator),
) -> DocumentActionProposalView:
    container = _container(request)
    _enabled(container)
    canonical_proposal_id = _proposal_id(proposal_id)
    proposal = (
        container.repository.get_action_proposal(proposal_id=canonical_proposal_id)
        if container.repository
        else None
    )
    if proposal is None or str(proposal.get("owner_id")) != principal.user_id:
        raise HTTPException(status_code=404, detail="document_action_proposal_not_found")
    try:
        result = container.repository.mark_action_proposal_executed(
            proposal_id=canonical_proposal_id,
            review_id=body.review_id,
            execution_ref=body.execution_ref,
            target_item_ref=body.target_item_ref,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    result["owner_id"] = principal.user_id
    return DocumentActionProposalView(**result)


@router.get("/{document_id}/source")
async def download_document_source(
    document_id: str,
    request: Request,
    principal: RequestPrincipal = Depends(require_operator),
) -> StreamingResponse:
    container = _container(request)
    _enabled(container)
    if container.repository is None or container.archive_reader is None:
        raise HTTPException(status_code=503, detail="document_source_unavailable")
    record = container.repository.get(_document_id(document_id), owner_id=principal.user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    if not DocumentAccessPolicy.can_read_source(record=record, user_id=principal.user_id):
        raise HTTPException(status_code=403, detail="protected_source_unavailable")
    if not record.source_ref:
        raise HTTPException(status_code=409, detail="document_source_not_ready")
    source = container.repository.archive_source(record.source_ref)
    if source is None or source.document_id != record.document_id:
        raise HTTPException(status_code=409, detail="document_source_mapping_invalid")
    filename = quote(record.original_filename, safe="")
    return StreamingResponse(
        _verified_source(
            container.archive_reader.download_original(source.external_id),
            expected_size=record.size_bytes,
            expected_sha256=record.sha256,
        ),
        media_type=record.media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Document-SHA256": record.sha256,
            "Content-Length": str(record.size_bytes),
        },
    )
