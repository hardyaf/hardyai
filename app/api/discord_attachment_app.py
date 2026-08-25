from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.operator_auth import require_operator, validate_security_configuration
from app.api.principals import RequestPrincipal
from app.api.security_headers import SECURITY_HEADERS
from app.config import settings
from app.integrations.discord_attachment.service import (
    DiscordAttachmentIngressError,
    DiscordAttachmentTransferService,
)
from app.integrations.discord_attachment.types import (
    DiscordAttachmentDescriptor,
    DiscordAttachmentReceipt,
)
from app.services.offline_runtime_policy import validate_offline_runtime


@asynccontextmanager
async def _lifespan(application: FastAPI):
    validate_security_configuration()
    validate_offline_runtime(settings, entrypoint="discord-attachment-ingress")
    if not settings.discord_attachment_ingress_enabled:
        raise RuntimeError("Discord attachment ingress is disabled")
    if application.state.transfer_service is None:
        application.state.transfer_service = DiscordAttachmentTransferService.from_settings(settings)
    try:
        yield
    finally:
        service = application.state.transfer_service
        if service is not None:
            await service.close()


def create_discord_attachment_app(
    transfer_service: DiscordAttachmentTransferService | None = None,
) -> FastAPI:
    application = FastAPI(
        title="HardyAI Discord Attachment Ingress",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    application.state.transfer_service = transfer_service

    @application.middleware("http")
    async def _guard_and_headers(request: Request, call_next):
        response = None
        if request.url.path == "/discord-attachments":
            raw_length = str(request.headers.get("content-length") or "").strip()
            if not raw_length.isdigit() or int(raw_length) > 8192:
                response = JSONResponse(
                    status_code=413,
                    content={"detail": "attachment_descriptor_too_large"},
                )
            elif str(request.headers.get("content-type") or "").split(";", 1)[0].casefold() != "application/json":
                response = JSONResponse(
                    status_code=415,
                    content={"detail": "application_json_required"},
                )
        if response is None:
            response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        response.headers["Cache-Control"] = "no-store"
        return response

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/discord-attachments", response_model=DiscordAttachmentReceipt)
    async def submit_attachment(
        request: Request,
        descriptor: DiscordAttachmentDescriptor,
        _principal: RequestPrincipal = Depends(require_operator),
    ) -> DiscordAttachmentReceipt:
        service = request.app.state.transfer_service
        if service is None:
            raise HTTPException(status_code=503, detail="discord_attachment_ingress_unavailable")
        try:
            return await service.submit(descriptor)
        except DiscordAttachmentIngressError as exc:
            status_code = 413 if exc.code == "discord_attachment_too_large" else 400
            if exc.code.startswith("document_gateway_"):
                status_code = 502
            raise HTTPException(status_code=status_code, detail=exc.code) from exc

    return application


app = create_discord_attachment_app()
