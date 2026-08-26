from __future__ import annotations

import base64
import binascii
import hmac
import json
import multiprocessing
import os
import re
import tempfile
from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from PIL import Image
from pydantic import BaseModel, Field


FRAMEWORK_VERSION = "3.6.0"
PIPELINE_VERSION = "1.6"
MODEL_NAME = "PaddleOCR-VL-1.6-0.9B"
_LAYOUT_MODEL_DIR = Path(
    os.getenv(
        "PADDLEOCR_VL_LAYOUT_MODEL_DIR",
        str(Path("/", "home", "paddleocr", ".paddlex", "official_models", "PP-DocLayoutV3")),
    )
)
_VLM_MODEL_DIR = Path(
    os.getenv(
        "PADDLEOCR_VL_MODEL_DIR",
        str(Path("/", "home", "paddleocr", ".paddlex", "official_models", "PaddleOCR-VL-1.6")),
    )
)
_KEY_PATH = Path(
    os.getenv("ACCELERATOR_ADMISSION_API_KEY_PATH", "/run/secrets/accelerator_admission_api_key")
)
_MAX_BYTES = max(1024, min(int(os.getenv("PADDLEOCR_VL_MAX_INPUT_BYTES", "52428800")), 104857600))
_MAX_PIXELS = max(
    1_000_000,
    min(int(os.getenv("PADDLEOCR_VL_MAX_IMAGE_PIXELS", "64000000")), 100_000_000),
)
_TIMEOUT_SECONDS = max(5.0, min(float(os.getenv("PADDLEOCR_VL_TIMEOUT_SECONDS", "90")), 180.0))
_MAX_NEW_TOKENS = max(64, min(int(os.getenv("PADDLEOCR_VL_MAX_NEW_TOKENS", "512")), 4096))
_INFERENCE_LOCK = Lock()


class VLMRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=180)
    media_type: str = Field(pattern=r"^image/(jpeg|png)$")
    file_base64: str = Field(min_length=4)


def _read_key() -> str:
    if _KEY_PATH.is_symlink():
        raise RuntimeError("paddleocr_vl_key_path_symlink")
    value = _KEY_PATH.resolve().read_text(encoding="utf-8").strip()
    if not value or len(value) > 512 or any(character.isspace() for character in value):
        raise RuntimeError("paddleocr_vl_key_invalid")
    return value


def _require_key(
    x_hardyai_accelerator_key: str | None = Header(default=None),
) -> None:
    try:
        configured = _read_key()
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="paddleocr_vl_key_unavailable") from exc
    supplied = str(x_hardyai_accelerator_key or "").strip()
    if not supplied or not hmac.compare_digest(configured, supplied):
        raise HTTPException(status_code=401, detail="paddleocr_vl_auth_required")


def _bbox(value: object) -> list[float] | None:
    if not isinstance(value, list) or len(value) < 4:
        return None
    try:
        if all(isinstance(item, (int, float)) for item in value[:4]):
            left, top, right, bottom = (float(item) for item in value[:4])
        else:
            points = [item for item in value if isinstance(item, list) and len(item) >= 2]
            xs = [float(item[0]) for item in points]
            ys = [float(item[1]) for item in points]
            left, top, right, bottom = min(xs), min(ys), max(xs), max(ys)
        if left <= right and top <= bottom:
            return [left, top, right, bottom]
    except (TypeError, ValueError):
        pass
    return None


def _result_dict(result: Any) -> dict[str, Any]:
    raw = getattr(result, "json", None)
    raw = raw() if callable(raw) else raw
    if not isinstance(raw, dict):
        try:
            raw = dict(result)
        except (TypeError, ValueError):
            raw = {}
    return raw.get("res") if isinstance(raw.get("res"), dict) else raw


def _run_pipeline(input_path: str, output_path: str) -> None:
    try:
        from paddleocr import PaddleOCRVL

        pipeline = PaddleOCRVL(
            pipeline_version="v1.6",
            layout_detection_model_dir=str(_LAYOUT_MODEL_DIR),
            vl_rec_model_dir=str(_VLM_MODEL_DIR),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            device="gpu",
        )
        pages: list[dict[str, Any]] = []
        total_characters = 0
        for page_index, result in enumerate(
            pipeline.predict(input_path, max_new_tokens=_MAX_NEW_TOKENS)
        ):
            raw = _result_dict(result)
            try:
                width = max(1, int(raw.get("width") or 1))
                height = max(1, int(raw.get("height") or 1))
            except (TypeError, ValueError):
                width, height = 1, 1
            raw_blocks = raw.get("parsing_res_list")
            blocks: list[dict[str, Any]] = []
            if isinstance(raw_blocks, list):
                for item in raw_blocks[:1000]:
                    if not isinstance(item, dict):
                        continue
                    text = " ".join(str(item.get("block_content") or "").split())[:10_000]
                    if not text:
                        continue
                    remaining = 1_000_000 - total_characters
                    if remaining <= 0:
                        break
                    text = text[:remaining]
                    total_characters += len(text)
                    label = str(item.get("block_label") or "vlm_block").strip().casefold()
                    if not re.fullmatch(r"[a-z0-9_-]{1,60}", label):
                        label = "vlm_block"
                    confidence = item.get("score")
                    if not isinstance(confidence, (int, float)):
                        confidence = None
                    blocks.append(
                        {
                            "kind": label,
                            "text": text,
                            "bbox": _bbox(item.get("block_bbox") or item.get("bbox")),
                            "confidence": (
                                max(0.0, min(float(confidence), 1.0))
                                if confidence is not None
                                else None
                            ),
                        }
                    )
            pages.append(
                {
                    "page_index": int(raw.get("page_index") or page_index),
                    "width": width,
                    "height": height,
                    "blocks": blocks,
                }
            )
        value = {
            "status": "success",
            "provider": "paddleocr_vl",
            "provider_version": FRAMEWORK_VERSION,
            "pipeline_version": PIPELINE_VERSION,
            "model": MODEL_NAME,
            "device": "gpu",
            "pages": pages,
        }
    except Exception as exc:
        value = {
            "status": "failure",
            "error_code": f"paddleocr_vl_{type(exc).__name__}"[:120],
        }
    Path(output_path).write_text(
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )


def _validate_image(payload: bytes, media_type: str) -> tuple[int, int, str]:
    if not payload or len(payload) > _MAX_BYTES:
        raise ValueError("paddleocr_vl_input_size_invalid")
    try:
        with Image.open(BytesIO(payload)) as image:
            image.verify()
        with Image.open(BytesIO(payload)) as image:
            width, height = int(image.width), int(image.height)
            observed = str(image.format or "").casefold()
    except Exception as exc:
        raise ValueError("paddleocr_vl_image_invalid") from exc
    allowed = {"jpeg", "mpo"} if media_type == "image/jpeg" else {"png"}
    if observed not in allowed or width <= 0 or height <= 0 or width * height > _MAX_PIXELS:
        raise ValueError("paddleocr_vl_image_policy_rejected")
    return width, height, ".jpg" if media_type == "image/jpeg" else ".png"


def _infer(payload: bytes, *, media_type: str) -> dict[str, Any]:
    _, _, suffix = _validate_image(payload, media_type)
    with tempfile.TemporaryDirectory(prefix="hardyai-vl-") as temporary:
        root = Path(temporary)
        input_path = root / f"input{suffix}"
        output_path = root / "result.json"
        input_path.write_bytes(payload)
        context = multiprocessing.get_context("spawn")
        process = context.Process(target=_run_pipeline, args=(str(input_path), str(output_path)))
        process.start()
        process.join(_TIMEOUT_SECONDS)
        if process.is_alive():
            process.terminate()
            process.join(5.0)
            if process.is_alive():
                process.kill()
                process.join(5.0)
            raise RuntimeError("paddleocr_vl_timeout")
        if process.exitcode != 0 or not output_path.is_file():
            raise RuntimeError("paddleocr_vl_process_failed")
        if output_path.stat().st_size > 16 * 1024 * 1024:
            raise RuntimeError("paddleocr_vl_response_too_large")
        value = json.loads(output_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("paddleocr_vl_response_invalid")
        return value


app = FastAPI(
    title="HardyAI PaddleOCR-VL",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def _bounded_request(request: Request, call_next):
    if request.method.upper() == "POST" and request.url.path == "/parse-image":
        raw = str(request.headers.get("content-length") or "").strip()
        maximum = ((_MAX_BYTES + 2) * 4 // 3) + 4096
        if not raw.isdigit():
            return JSONResponse(status_code=411, content={"detail": "content_length_required"})
        if int(raw) <= 0 or int(raw) > maximum:
            return JSONResponse(status_code=413, content={"detail": "paddleocr_vl_request_too_large"})
    return await call_next(request)


@app.get("/ready")
def ready(_: None = Depends(_require_key)) -> dict[str, Any]:
    models_local = _LAYOUT_MODEL_DIR.is_dir() and _VLM_MODEL_DIR.is_dir()
    return {
        "status": "ready" if models_local else "degraded",
        "provider_version": FRAMEWORK_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "model": MODEL_NAME,
        "device": "gpu",
        "models_local": models_local,
        "execution": "one_request_subprocess",
    }


@app.post("/parse-image")
def parse_image(body: VLMRequest, _: None = Depends(_require_key)) -> dict[str, Any]:
    if (
        Path(body.filename).name != body.filename
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,179}", body.filename)
        or any(ord(character) < 32 for character in body.filename)
    ):
        raise HTTPException(status_code=400, detail="paddleocr_vl_filename_invalid")
    extension = Path(body.filename).suffix.casefold()
    expected = {".jpg", ".jpeg"} if body.media_type == "image/jpeg" else {".png"}
    if extension not in expected:
        raise HTTPException(status_code=400, detail="paddleocr_vl_extension_mismatch")
    try:
        payload = base64.b64decode(body.file_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="paddleocr_vl_base64_invalid") from exc
    try:
        with _INFERENCE_LOCK:
            value = _infer(payload, media_type=body.media_type)
    except (RuntimeError, ValueError) as exc:
        code = str(exc)
        if not re.fullmatch(r"[a-z0-9_]{1,120}", code):
            code = "paddleocr_vl_inference_failed"
        raise HTTPException(status_code=400 if isinstance(exc, ValueError) else 503, detail=code) from exc
    if value.get("status") != "success":
        code = str(value.get("error_code") or "paddleocr_vl_inference_failed")[:120]
        raise HTTPException(status_code=503, detail=code)
    return value
