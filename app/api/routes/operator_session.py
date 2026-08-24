from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request, Response

from app.api.operator_auth import (
    OPERATOR_COOKIE_NAME,
    authenticate_operator_header,
    csrf_token_for_session,
    issue_operator_session,
    require_operator,
)
from app.config import settings


router = APIRouter(prefix="/operator/session", tags=["operator-session"])


@router.post("")
async def create_operator_session(
    response: Response,
    x_jarvis_operator_key: str | None = Header(default=None),
) -> dict[str, object]:
    authenticate_operator_header(x_jarvis_operator_key)
    session_token, csrf_token = issue_operator_session()
    response.set_cookie(
        key=OPERATOR_COOKIE_NAME,
        value=session_token,
        httponly=True,
        secure=str(settings.app_env).strip().casefold() == "production",
        samesite="strict",
        max_age=max(60, int(settings.operator_session_ttl_seconds)),
        path="/",
    )
    return {"authenticated": True, "csrf_token": csrf_token}


@router.get("")
async def inspect_operator_session(request: Request) -> dict[str, object]:
    session_token = str(request.cookies.get(OPERATOR_COOKIE_NAME) or "").strip()
    return {
        "authenticated": True,
        "csrf_token": csrf_token_for_session(session_token),
    }


@router.delete("", dependencies=[Depends(require_operator)])
async def delete_operator_session(response: Response) -> dict[str, object]:
    response.delete_cookie(OPERATOR_COOKIE_NAME, path="/")
    return {"authenticated": False}
