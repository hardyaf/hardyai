from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import hashlib
import json
import os
import tempfile
from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


VERSION = "3.7.0"
_MEDIA_SUFFIX = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}


def _image_format_allowed(*, media_type: str, observed: str) -> bool:
    """Match Pillow formats without rejecting JPEG-compatible phone-camera MPO files."""
    allowed = {"jpeg", "mpo"} if media_type == "image/jpeg" else {"png"}
    return observed in allowed


class OCRRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=180)
    media_type: str = Field(min_length=1, max_length=100)
    file_base64: str = Field(min_length=4)


def _read_api_key() -> str:
    path = Path(os.getenv("PADDLEOCR_API_KEY_PATH", "/run/secrets/paddleocr_api_key"))
    if path.is_symlink():
        raise RuntimeError("PaddleOCR API key path must not be a symlink")
    value = path.resolve().read_text(encoding="utf-8").strip()
    if not value or len(value) > 512 or any(character.isspace() for character in value):
        raise RuntimeError("PaddleOCR API key is invalid")
    return value


def _require_key(x_api_key: str | None = Header(default=None)) -> None:
    try:
        configured = _read_api_key()
    except OSError as exc:
        raise HTTPException(status_code=503, detail="paddleocr_key_unavailable") from exc
    if not x_api_key or not hmac.compare_digest(configured, str(x_api_key).strip()):
        raise HTTPException(status_code=401, detail="paddleocr_auth_required")


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    to_list = getattr(value, "tolist", None)
    if callable(to_list):
        return _jsonable(to_list())
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _jsonable(item())
        except (TypeError, ValueError):
            pass
    return str(value)


class _OCREngine:
    def __init__(self) -> None:
        self._lock = Lock()
        self._pipeline: Any | None = None
        self._error: str | None = None
        self._tier = str(os.getenv("PADDLEOCR_MODEL_TIER", "small")).strip().casefold()
        if self._tier not in {"tiny", "small", "medium"}:
            raise RuntimeError("PADDLEOCR_MODEL_TIER must be tiny, small, or medium")
        self._model_root = Path(os.getenv("PADDLEOCR_MODEL_ROOT", "/models/official_models")).resolve()
        self._det_dir = self._model_root / f"PP-OCRv6_{self._tier}_det"
        self._rec_dir = self._model_root / f"PP-OCRv6_{self._tier}_rec"
        self._max_bytes = max(1024, min(int(os.getenv("PADDLEOCR_MAX_INPUT_BYTES", "52428800")), 104857600))
        self._max_pixels = max(1_000_000, min(int(os.getenv("PADDLEOCR_MAX_IMAGE_PIXELS", "64000000")), 100_000_000))
        self._max_pages = max(1, min(int(os.getenv("PADDLEOCR_MAX_PAGES", "100")), 200))

    def load(self) -> None:
        if not self._det_dir.is_dir() or not self._rec_dir.is_dir():
            self._error = "paddleocr_model_files_missing"
            return
        if not self._verify_manifest():
            self._error = "paddleocr_model_manifest_invalid"
            return
        try:
            from paddleocr import PaddleOCR

            self._pipeline = PaddleOCR(
                text_detection_model_name=f"PP-OCRv6_{self._tier}_det",
                text_detection_model_dir=str(self._det_dir),
                text_recognition_model_name=f"PP-OCRv6_{self._tier}_rec",
                text_recognition_model_dir=str(self._rec_dir),
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                device="cpu",
                cpu_threads=max(1, min(int(os.getenv("PADDLEOCR_CPU_THREADS", "4")), 16)),
            )
        except Exception as exc:  # fail closed; readiness exposes only a bounded code
            self._error = f"paddleocr_load_{type(exc).__name__}"[:120]

    def _verify_manifest(self) -> bool:
        manifest_path = self._model_root.parent / "model-manifest.json"
        try:
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(value, dict) or str(value.get("paddleocr_version") or "") != VERSION:
            return False
        rows = value.get("files")
        if not isinstance(rows, list):
            return False
        prefixes = {
            f"official_models/PP-OCRv6_{self._tier}_det/",
            f"official_models/PP-OCRv6_{self._tier}_rec/",
        }
        selected = [
            row for row in rows
            if isinstance(row, dict)
            and any(str(row.get("path") or "").startswith(prefix) for prefix in prefixes)
        ]
        if not selected:
            return False
        root = self._model_root.parent
        for row in selected:
            relative = str(row.get("path") or "")
            path = (root / relative).resolve()
            if root not in path.parents or path.is_symlink() or not path.is_file():
                return False
            try:
                if path.stat().st_size != int(row.get("size_bytes")):
                    return False
            except (TypeError, ValueError):
                return False
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if not hmac.compare_digest(digest.hexdigest(), str(row.get("sha256") or "")):
                return False
        return True

    def ready(self) -> bool:
        return self._pipeline is not None and self._error is None

    def readiness(self) -> dict[str, Any]:
        return {
            "status": "ready" if self.ready() else "degraded",
            "version": VERSION,
            "device": "cpu",
            "model_tier": self._tier,
            "models_local": self._det_dir.is_dir() and self._rec_dir.is_dir(),
            "error_code": self._error,
        }

    def _validate_source(self, payload: bytes, media_type: str) -> tuple[int | None, int | None]:
        if not payload or len(payload) > self._max_bytes:
            raise ValueError("paddleocr_input_size_invalid")
        if media_type == "application/pdf":
            if not payload.startswith(b"%PDF-"):
                raise ValueError("paddleocr_pdf_signature_invalid")
            from pypdf import PdfReader

            try:
                reader = PdfReader(BytesIO(payload), strict=False)
            except Exception as exc:
                raise ValueError("paddleocr_pdf_invalid") from exc
            if reader.is_encrypted or not reader.pages or len(reader.pages) > self._max_pages:
                raise ValueError("paddleocr_pdf_page_policy_rejected")
            return None, None
        from PIL import Image

        try:
            with Image.open(BytesIO(payload)) as image:
                image.verify()
            with Image.open(BytesIO(payload)) as image:
                width, height = int(image.width), int(image.height)
                observed = str(image.format or "").casefold()
        except Exception as exc:
            raise ValueError("paddleocr_image_invalid") from exc
        if (
            not _image_format_allowed(media_type=media_type, observed=observed)
            or width <= 0
            or height <= 0
            or width * height > self._max_pixels
        ):
            raise ValueError("paddleocr_image_policy_rejected")
        return width, height

    @staticmethod
    def _result_dict(result: Any) -> dict[str, Any]:
        raw = getattr(result, "json", None)
        if callable(raw):
            raw = raw()
        if raw is None:
            raw = getattr(result, "to_json", None)
            if callable(raw):
                raw = raw()
        if not isinstance(raw, dict):
            try:
                raw = dict(result)
            except (TypeError, ValueError):
                raw = {}
        inner = raw.get("res") if isinstance(raw.get("res"), dict) else raw
        allowlisted = {
            key: _jsonable(inner.get(key))
            for key in ("page_index", "dt_polys", "dt_scores", "rec_texts", "rec_scores", "rec_polys", "rec_boxes")
            if key in inner
        }
        return allowlisted

    def infer(self, *, payload: bytes, filename: str, media_type: str) -> dict[str, Any]:
        if not self.ready():
            raise RuntimeError("paddleocr_not_ready")
        if media_type not in _MEDIA_SUFFIX:
            raise ValueError("paddleocr_media_type_unsupported")
        if Path(filename).suffix.casefold() not in ({".jpg", ".jpeg"} if media_type == "image/jpeg" else {_MEDIA_SUFFIX[media_type]}):
            raise ValueError("paddleocr_extension_mismatch")
        width, height = self._validate_source(payload, media_type)
        descriptor, temporary_name = tempfile.mkstemp(prefix="hardyai-ocr-", suffix=_MEDIA_SUFFIX[media_type])
        path = Path(temporary_name)
        try:
            os.chmod(path, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            with self._lock:
                results = list(self._pipeline.predict(str(path)))
            pages: list[dict[str, Any]] = []
            for index, result in enumerate(results[: self._max_pages]):
                page = self._result_dict(result)
                page["page_index"] = int(page.get("page_index")) if page.get("page_index") is not None else index
                boxes = page.get("rec_boxes") or page.get("rec_polys") or []
                coordinates = [number for box in boxes if isinstance(box, list) for number in _flatten_numbers(box)]
                page["width"] = width or (max(coordinates[0::2], default=1.0))
                page["height"] = height or (max(coordinates[1::2], default=1.0))
                pages.append(page)
            return {
                "status": "success",
                "provider": "paddleocr",
                "provider_version": VERSION,
                "model": f"PP-OCRv6_{self._tier}",
                "device": "cpu",
                "language": "multilingual",
                "pages": pages,
            }
        finally:
            path.unlink(missing_ok=True)


def _flatten_numbers(value: Any) -> list[float]:
    numbers: list[float] = []
    if isinstance(value, list):
        for item in value:
            numbers.extend(_flatten_numbers(item))
    elif isinstance(value, (int, float)):
        numbers.append(float(value))
    return numbers


engine = _OCREngine()
app = FastAPI(title="HardyAI Conventional OCR", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def _bounded_request(request: Request, call_next):
    if request.method.upper() != "POST" or request.url.path != "/ocr":
        return await call_next(request)
    raw_length = str(request.headers.get("content-length") or "").strip()
    maximum = int(os.getenv("PADDLEOCR_MAX_INPUT_BYTES", "52428800")) * 2
    if not raw_length.isdigit() or int(raw_length) <= 0:
        return JSONResponse(status_code=411, content={"detail": "content_length_required"})
    if int(raw_length) > maximum:
        return JSONResponse(status_code=413, content={"detail": "paddleocr_request_too_large"})
    return await call_next(request)


@app.on_event("startup")
async def _startup() -> None:
    await asyncio.to_thread(engine.load)


@app.get("/ready", dependencies=[Depends(_require_key)])
async def ready() -> dict[str, Any]:
    value = engine.readiness()
    if value["status"] != "ready":
        raise HTTPException(status_code=503, detail=value)
    return value


@app.post("/ocr", dependencies=[Depends(_require_key)])
async def infer(request: OCRRequest) -> dict[str, Any]:
    try:
        payload = base64.b64decode(request.file_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="paddleocr_base64_invalid") from exc
    try:
        return await asyncio.to_thread(
            engine.infer,
            payload=payload,
            filename=request.filename,
            media_type=request.media_type.casefold(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:120]) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)[:120]) from exc
