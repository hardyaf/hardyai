from __future__ import annotations

from app.skills.domains.documents.ingestion import TransientDocumentSpool
from app.skills.domains.documents.reprocessing import DocumentReprocessingService
from app.skills.domains.documents.service import DocumentIngestionService
from app.skills.domains.documents.storage import DocumentRepository
from app.skills.domains.documents.types import ProcessingRoute


PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x03\x00\x00\x00\x02"
    b"\x08\x02\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


class IntakeEnqueuer:
    def enqueue_document(self, *, document_id: str, intake_id: str, sha256: str) -> str:
        return "11111111-1111-4111-8111-111111111111"


class ProcessingEnqueuer:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def enqueue_processing(self, **kwargs) -> str:
        self.calls.append(dict(kwargs))
        return "22222222-2222-4222-8222-222222222222"


def test_review_fallback_appends_idempotent_gpu_run_linked_to_cpu_evidence(tmp_path) -> None:
    repository = DocumentRepository(str(tmp_path / "documents.db"))
    spool = TransientDocumentSpool(
        str(tmp_path / "spool"),
        max_bytes=1024,
        quota_bytes=4096,
        max_image_pixels=6,
    )
    writer = spool.begin(
        filename="card.png",
        declared_media_type="image/png",
        title="Business card",
    )
    writer.write(PNG)
    accepted = DocumentIngestionService(
        repository=repository,
        spool=spool,
        enqueuer=IntakeEnqueuer(),
    ).accept(owner_id="operator", staged=writer.finish())
    document_id = accepted.record.document_id
    repository.mark_archiving(document_id=document_id, task_ref="paperless-task")
    ready = repository.mark_ready(
        document_id=document_id,
        provider="paperless",
        external_id="paperless-1",
        verified_sha256=accepted.record.sha256,
    )
    cpu = repository.create_processing_run(
        document_id=document_id,
        route=ProcessingRoute.CONVENTIONAL_OCR,
        parser_name="paddleocr",
        parser_version="3.7.0",
        parser_image_digest="sha256:cpu",
        configuration_sha256="c" * 64,
        resource_lane="cpu_ocr",
        request_key="cpu-card-1",
    )
    enqueuer = ProcessingEnqueuer()
    service = DocumentReprocessingService(
        repository=repository,
        enqueuer=enqueuer,
        parser_name="docling",
        parser_version="1",
        parser_image_digest="sha256:docling",
        configuration_sha256="d" * 64,
        conventional_parser_name="paddleocr",
        conventional_parser_version="3.7.0",
        conventional_parser_image_digest="sha256:cpu",
        conventional_configuration_sha256="c" * 64,
        review_fallback_parser_name="paddleocr-vl",
        review_fallback_parser_version="3.6.0",
        review_fallback_parser_image_digest="sha256:gpu",
        review_fallback_configuration_sha256="g" * 64,
    )

    first = service.request(
        document_id=document_id,
        owner_id=ready.owner_id,
        idempotency_key="discord:feedback-1",
        processing_tier="review_fallback",
    )
    repeated = service.request(
        document_id=document_id,
        owner_id=ready.owner_id,
        idempotency_key="discord:feedback-1",
        processing_tier="review_fallback",
    )

    assert first["run_id"] == repeated["run_id"]
    assert first["route"] == ProcessingRoute.VLM_FALLBACK.value
    assert first["processing_tier"] == "review_fallback"
    run = repository.get_processing_run(first["run_id"])
    assert run is not None
    assert run["fallback_from_run_id"] == cpu["run_id"]
    assert run["resource_lane"] == "gpu_vlm"
    assert len(enqueuer.calls) == 2
    assert all(call["run_id"] == first["run_id"] for call in enqueuer.calls)
    repository.close()
