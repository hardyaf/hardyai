from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.api.document_limits import DocumentRequestGuard
from app.api.operator_auth import validate_security_configuration
from app.api.routes.documents import router as documents_router
from app.api.routes.operator_session import router as operator_session_router
from app.api.security_headers import SECURITY_HEADERS
from app.composition.documents import DocumentGatewayContainer
from app.config import settings
from app.services.offline_runtime_policy import validate_offline_runtime


@asynccontextmanager
async def _lifespan(application: FastAPI):
    validate_security_configuration()
    validate_offline_runtime(
        application.state.document_container.settings,
        entrypoint="document-gateway",
    )
    try:
        yield
    finally:
        application.state.document_container.close()


def create_document_app(container: DocumentGatewayContainer | None = None) -> FastAPI:
    document_container = container or DocumentGatewayContainer.from_settings(settings)
    application = FastAPI(
        title="HardyAI Document Gateway",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    application.state.document_container = document_container

    @application.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        response.headers["Cache-Control"] = "no-store"
        return response

    application.include_router(operator_session_router)
    application.include_router(documents_router)
    return DocumentRequestGuard(
        application,
        max_request_bytes=(
            document_container.settings.documents_max_upload_bytes
            + document_container.settings.documents_max_request_overhead_bytes
        ),
        body_timeout_seconds=document_container.settings.documents_body_timeout_seconds,
        global_concurrency=document_container.settings.documents_global_concurrency,
        per_principal_concurrency=document_container.settings.documents_per_principal_concurrency,
        app_env=document_container.settings.app_env,
    )


app = create_document_app()
