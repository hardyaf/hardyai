from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from fastapi import Cookie, Header, HTTPException, Request

from app.api.principals import PrincipalKind, RequestPrincipal
from app.config import settings


OPERATOR_COOKIE_NAME = "jarvis_operator_session"


def _configured_key() -> str:
    return str(getattr(settings, "operator_api_key", "") or "").strip()


def _session_ttl_seconds() -> int:
    return max(60, int(getattr(settings, "operator_session_ttl_seconds", 3600)))


def _test_principal() -> RequestPrincipal:
    return RequestPrincipal(
        subject="test-operator",
        kind=PrincipalKind.TEST,
        user_id="local_user",
        source="dashboard",
        scopes=frozenset({"operator", "ask", "house", "debug"}),
        authenticated_by="test_mode",
    )


def _operator_principal(authenticated_by: str) -> RequestPrincipal:
    return RequestPrincipal(
        subject="operator",
        kind=PrincipalKind.OPERATOR,
        user_id="operator",
        source="dashboard",
        scopes=frozenset({"operator", "ask", "house", "debug"}),
        authenticated_by=authenticated_by,
    )


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _sign(value: str, *, purpose: str) -> str:
    return _b64(
        hmac.new(
            _configured_key().encode("utf-8"),
            f"{purpose}:{value}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
    )


def issue_operator_session() -> tuple[str, str]:
    if not _configured_key():
        raise HTTPException(status_code=503, detail="operator_api_key_not_configured")
    issued_at = int(time.time())
    body = f"v1.{issued_at}.{secrets.token_urlsafe(24)}"
    token = f"{body}.{_sign(body, purpose='session')}"
    return token, _sign(token, purpose="csrf")


def _verify_session(token: str) -> bool:
    parts = str(token or "").split(".")
    if len(parts) != 4 or parts[0] != "v1":
        return False
    body = ".".join(parts[:3])
    if not hmac.compare_digest(_sign(body, purpose="session"), parts[3]):
        return False
    try:
        issued_at = int(parts[1])
    except ValueError:
        return False
    now = int(time.time())
    return 0 <= now - issued_at <= _session_ttl_seconds()


def csrf_token_for_session(token: str) -> str:
    if not _verify_session(token):
        raise HTTPException(status_code=401, detail="operator_auth_required")
    return _sign(token, purpose="csrf")


def authenticate_operator_header(x_jarvis_operator_key: str | None) -> RequestPrincipal:
    configured = _configured_key()
    supplied = str(x_jarvis_operator_key or "").strip()
    if not configured:
        if str(getattr(settings, "app_env", "development")).strip().casefold() == "test":
            return _test_principal()
        raise HTTPException(status_code=503, detail="operator_api_key_not_configured")
    if not supplied or not hmac.compare_digest(configured, supplied):
        raise HTTPException(status_code=401, detail="operator_auth_required")
    return _operator_principal("api_key")


def require_operator(
    request: Request,
    x_jarvis_operator_key: str | None = Header(default=None),
    jarvis_operator_session: str | None = Cookie(default=None, alias=OPERATOR_COOKIE_NAME),
    x_csrf_token: str | None = Header(default=None),
) -> RequestPrincipal:
    return require_operator_session_or_key(
        request=request,
        x_jarvis_operator_key=x_jarvis_operator_key,
        jarvis_operator_session=jarvis_operator_session,
        x_csrf_token=x_csrf_token,
    )


def require_operator_session_or_key(
    request: Request,
    x_jarvis_operator_key: str | None = Header(default=None),
    jarvis_operator_session: str | None = Cookie(default=None, alias=OPERATOR_COOKIE_NAME),
    x_csrf_token: str | None = Header(default=None),
) -> RequestPrincipal:
    if x_jarvis_operator_key:
        return authenticate_operator_header(x_jarvis_operator_key)

    configured = _configured_key()
    if not configured:
        if str(getattr(settings, "app_env", "development")).strip().casefold() == "test":
            return _test_principal()
        raise HTTPException(status_code=503, detail="operator_api_key_not_configured")

    session_token = str(jarvis_operator_session or "").strip()
    if not session_token or not _verify_session(session_token):
        raise HTTPException(status_code=401, detail="operator_auth_required")
    if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        expected = _sign(session_token, purpose="csrf")
        supplied = str(x_csrf_token or "").strip()
        if not supplied or not hmac.compare_digest(expected, supplied):
            raise HTTPException(status_code=403, detail="csrf_validation_failed")
    return _operator_principal("session_cookie")


def validate_security_configuration() -> None:
    if (
        str(getattr(settings, "app_env", "development")).strip().casefold() == "production"
        and not _configured_key()
    ):
        raise RuntimeError("APP_ENV=production requires JARVIS_OPERATOR_API_KEY.")
