from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.operator_auth import require_operator
from app.api.principals import RequestPrincipal
from app.dependencies import get_provenance_repository
from app.provenance.repository import ProvenanceRepository


router = APIRouter(prefix="/provenance", tags=["provenance"])


@router.get("/lists/items/{item_id}")
async def list_item_provenance(
    item_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    _: RequestPrincipal = Depends(require_operator),
    repository: ProvenanceRepository = Depends(get_provenance_repository),
) -> dict[str, Any]:
    """Return content-free source links for one canonical Lists item."""

    target_ref = str(item_id)[:240]
    return {
        "target_domain": "lists",
        "target_type": "list_item",
        "target_ref": target_ref,
        "links": repository.for_target(
            target_domain="lists",
            target_type="list_item",
            target_ref=target_ref,
            limit=limit,
        ),
    }
