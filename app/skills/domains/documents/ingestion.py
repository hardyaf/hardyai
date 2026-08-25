from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import unicodedata
from pathlib import Path
from threading import RLock
from uuid import uuid4

from app.skills.domains.documents.types import StagedDocument


class DocumentValidationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_TYPE_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
_PDF_FORBIDDEN_TOKENS = (b"/Encrypt", b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFile")


def sanitize_filename(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not normalized or len(normalized) > 180:
        raise DocumentValidationError("invalid_filename")
    if normalized in {".", ".."} or "/" in normalized or "\\" in normalized:
        raise DocumentValidationError("unsafe_filename")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise DocumentValidationError("unsafe_filename")
    if not re.fullmatch(r"[\w .()\[\]{}+,&'@#-]+", normalized, flags=re.UNICODE):
        raise DocumentValidationError("unsafe_filename")
    extension = Path(normalized).suffix.casefold()
    if extension not in _TYPE_BY_EXTENSION:
        raise DocumentValidationError("unsupported_extension")
    return normalized


def sanitize_title(value: str | None, *, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    if any(
        (ord(character) < 32 and character not in "\t\r\n") or ord(character) == 127
        for character in normalized
    ):
        raise DocumentValidationError("unsafe_title")
    compact = " ".join(normalized.split()).strip()
    return (compact or fallback)[:200]


def detect_media_type(prefix: bytes) -> str:
    if prefix.startswith(b"%PDF-"):
        return "application/pdf"
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    raise DocumentValidationError("unsupported_file_signature")


def _jpeg_dimensions(path: Path) -> tuple[int, int]:
    start_of_frame = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    with path.open("rb") as stream:
        if stream.read(2) != b"\xff\xd8":
            raise DocumentValidationError("malformed_jpeg")
        for _ in range(10000):
            marker_prefix = stream.read(1)
            while marker_prefix and marker_prefix != b"\xff":
                marker_prefix = stream.read(1)
            if not marker_prefix:
                break
            marker = stream.read(1)
            while marker == b"\xff":
                marker = stream.read(1)
            if not marker:
                break
            marker_value = marker[0]
            if marker_value in {0x00, 0x01, 0xD8, 0xD9} or 0xD0 <= marker_value <= 0xD7:
                continue
            raw_length = stream.read(2)
            if len(raw_length) != 2:
                break
            segment_length = int.from_bytes(raw_length, "big")
            if segment_length < 2:
                break
            if marker_value in start_of_frame:
                frame = stream.read(5)
                if len(frame) != 5:
                    break
                return int.from_bytes(frame[3:5], "big"), int.from_bytes(frame[1:3], "big")
            stream.seek(segment_length - 2, os.SEEK_CUR)
    raise DocumentValidationError("malformed_jpeg")


class StagingWriter:
    def __init__(
        self,
        *,
        root: Path,
        max_bytes: int,
        quota_bytes: int,
        filename: str,
        declared_media_type: str | None,
        title: str | None,
        quota_lock: RLock,
        min_free_bytes: int,
        max_image_pixels: int,
    ) -> None:
        self._root = root
        self._max_bytes = max(1, int(max_bytes))
        self._quota_bytes = max(self._max_bytes, int(quota_bytes))
        self._filename = sanitize_filename(filename)
        self._declared_media_type = str(declared_media_type or "").split(";", 1)[0].strip().casefold()
        self._title = sanitize_title(title, fallback=Path(self._filename).stem[:200])
        self._quota_lock = quota_lock
        self._min_free_bytes = max(0, int(min_free_bytes))
        self._max_image_pixels = max(1, int(max_image_pixels))
        with self._quota_lock:
            current_bytes = sum(path.stat().st_size for path in root.iterdir() if path.is_file())
            if current_bytes >= self._quota_bytes:
                raise DocumentValidationError("spool_quota_exceeded")
            if shutil.disk_usage(root).free < self._min_free_bytes + self._max_bytes:
                raise DocumentValidationError("spool_free_space_floor")
            descriptor, temporary = tempfile.mkstemp(prefix=".upload-", suffix=".part", dir=str(root))
        self._temporary_path = Path(temporary)
        try:
            os.chmod(temporary, 0o600)
            self._stream = os.fdopen(descriptor, "wb", buffering=0)
        except Exception:
            os.close(descriptor)
            self._temporary_path.unlink(missing_ok=True)
            raise
        self._hash = hashlib.sha256()
        self._prefix = bytearray()
        self._suffix = bytearray()
        self._scan_tail = b""
        self._pdf_forbidden_token: bytes | None = None
        self._size = 0
        self._closed = False

    def write(self, data: bytes) -> None:
        if self._closed:
            raise RuntimeError("staging writer is closed")
        if not data:
            return
        with self._quota_lock:
            next_size = self._size + len(data)
            if next_size > self._max_bytes:
                raise DocumentValidationError("document_too_large")
            current_bytes = sum(path.stat().st_size for path in self._root.iterdir() if path.is_file())
            if current_bytes + len(data) > self._quota_bytes:
                raise DocumentValidationError("spool_quota_exceeded")
            if shutil.disk_usage(self._root).free < self._min_free_bytes + len(data):
                raise DocumentValidationError("spool_free_space_floor")
            if len(self._prefix) < 4096:
                self._prefix.extend(data[: 4096 - len(self._prefix)])
            self._suffix = bytearray((bytes(self._suffix) + data)[-4096:])
            scan = self._scan_tail + data
            for token in _PDF_FORBIDDEN_TOKENS:
                if token in scan:
                    self._pdf_forbidden_token = token
                    break
            self._scan_tail = scan[-32:]
            self._hash.update(data)
            self._stream.write(data)
            self._size = next_size

    def finish(self, *, title: str | None = None) -> StagedDocument:
        if self._closed:
            raise RuntimeError("staging writer is closed")
        final_title = sanitize_title(title, fallback=self._title)
        if self._size == 0:
            self.abort()
            raise DocumentValidationError("empty_document")
        detected = detect_media_type(bytes(self._prefix))
        expected = _TYPE_BY_EXTENSION[Path(self._filename).suffix.casefold()]
        if expected != detected:
            self.abort()
            raise DocumentValidationError("extension_signature_mismatch")
        if self._declared_media_type and self._declared_media_type not in {
            detected,
            "application/octet-stream",
        }:
            self.abort()
            raise DocumentValidationError("declared_type_mismatch")
        self._stream.flush()
        os.fsync(self._stream.fileno())
        suffix = bytes(self._suffix).rstrip(b"\x00\t\r\n ")
        if detected == "application/pdf":
            if self._pdf_forbidden_token == b"/Encrypt":
                self.abort()
                raise DocumentValidationError("encrypted_pdf_not_supported")
            if self._pdf_forbidden_token is not None:
                self.abort()
                raise DocumentValidationError("active_or_embedded_pdf_not_supported")
            if not suffix.endswith(b"%%EOF"):
                self.abort()
                raise DocumentValidationError("malformed_pdf")
        elif detected == "image/jpeg":
            if not suffix.endswith(b"\xff\xd9"):
                self.abort()
                raise DocumentValidationError("malformed_jpeg")
            try:
                width, height = _jpeg_dimensions(self._temporary_path)
            except DocumentValidationError:
                self.abort()
                raise
            if width <= 0 or height <= 0 or width * height > self._max_image_pixels:
                self.abort()
                raise DocumentValidationError("image_dimensions_exceeded")
        elif detected == "image/png":
            if len(self._prefix) < 24:
                self.abort()
                raise DocumentValidationError("malformed_png")
            width = int.from_bytes(self._prefix[16:20], "big")
            height = int.from_bytes(self._prefix[20:24], "big")
            if width <= 0 or height <= 0 or width * height > self._max_image_pixels:
                self.abort()
                raise DocumentValidationError("image_dimensions_exceeded")
            if not suffix.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82"):
                self.abort()
                raise DocumentValidationError("malformed_png")
        with self._quota_lock:
            self._stream.close()
            final_key = f"{uuid4()}.bin"
            final_path = self._root / final_key
            os.replace(self._temporary_path, final_path)
        try:
            directory_fd = os.open(str(self._root), os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        self._closed = True
        return StagedDocument(
            spool_key=final_key,
            original_filename=self._filename,
            title=final_title,
            media_type=detected,
            size_bytes=self._size,
            sha256=self._hash.hexdigest(),
        )

    def abort(self) -> None:
        if self._closed:
            return
        with self._quota_lock:
            try:
                self._stream.close()
            finally:
                self._temporary_path.unlink(missing_ok=True)
                self._closed = True


class TransientDocumentSpool:
    """Fsynced transient staging; the configured root must live on encrypted storage."""

    def __init__(
        self,
        root: str,
        *,
        max_bytes: int,
        quota_bytes: int,
        min_free_bytes: int = 0,
        max_image_pixels: int = 64000000,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.max_bytes = max(1, int(max_bytes))
        self.quota_bytes = max(self.max_bytes, int(quota_bytes))
        self.min_free_bytes = max(0, int(min_free_bytes))
        self.max_image_pixels = max(1, int(max_image_pixels))
        self._quota_lock = RLock()

    def begin(
        self,
        *,
        filename: str,
        declared_media_type: str | None,
        title: str | None,
    ) -> StagingWriter:
        return StagingWriter(
            root=self.root,
            max_bytes=self.max_bytes,
            quota_bytes=self.quota_bytes,
            filename=filename,
            declared_media_type=declared_media_type,
            title=title,
            quota_lock=self._quota_lock,
            min_free_bytes=self.min_free_bytes,
            max_image_pixels=self.max_image_pixels,
        )

    def usage_bytes(self) -> int:
        with self._quota_lock:
            return sum(path.stat().st_size for path in self.root.iterdir() if path.is_file())

    def free_bytes(self) -> int:
        return int(shutil.disk_usage(self.root).free)

    def path_for(self, spool_key: str) -> Path:
        if not re.fullmatch(r"[0-9a-fA-F-]{36}\.bin", str(spool_key or "")):
            raise ValueError("invalid spool key")
        path = (self.root / spool_key).resolve()
        if path.parent != self.root:
            raise ValueError("invalid spool key")
        return path

    def delete(self, spool_key: str | None) -> None:
        if spool_key:
            self.path_for(spool_key).unlink(missing_ok=True)

    def open_read(self, spool_key: str):
        path = self.path_for(spool_key)
        if path.is_symlink():
            raise ValueError("spool symlinks are not allowed")
        return path.open("rb")
