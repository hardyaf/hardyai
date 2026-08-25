from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path


_SUFFIXES = {"json", "md", "txt"}
_KEY = re.compile(r"sha256/[0-9a-f]{2}/[0-9a-f]{64}\.(?:json|md|txt)")


@dataclass(frozen=True)
class StoredArtifact:
    storage_key: str
    sha256: str
    size_bytes: int


class ContentAddressedArtifactStore:
    """Immutable derivative storage beneath one generated, encrypted root."""

    def __init__(self, root: str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def put(self, payload: bytes, *, suffix: str) -> StoredArtifact:
        normalized_suffix = str(suffix or "").strip().casefold().lstrip(".")
        if normalized_suffix not in _SUFFIXES:
            raise ValueError("unsupported artifact suffix")
        digest = hashlib.sha256(payload).hexdigest()
        key = f"sha256/{digest[:2]}/{digest}.{normalized_suffix}"
        target = self.path_for(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(target.parent, 0o700)
        if target.exists():
            if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise RuntimeError("artifact_hash_conflict")
            return StoredArtifact(storage_key=key, sha256=digest, size_bytes=target.stat().st_size)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".artifact-", dir=str(target.parent))
        temporary = Path(temporary_name)
        try:
            os.chmod(temporary, 0o600)
            with os.fdopen(descriptor, "wb", buffering=0) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            if os.name != "nt":
                directory_fd = os.open(str(target.parent), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
        return StoredArtifact(storage_key=key, sha256=digest, size_bytes=len(payload))

    def path_for(self, storage_key: str) -> Path:
        normalized = str(storage_key or "").strip().casefold()
        if not _KEY.fullmatch(normalized):
            raise ValueError("invalid artifact storage key")
        path = (self.root / normalized).resolve()
        if self.root not in path.parents:
            raise ValueError("artifact storage key escapes root")
        return path

    def read(self, storage_key: str, *, max_bytes: int) -> bytes:
        path = self.path_for(storage_key)
        if path.is_symlink():
            raise ValueError("artifact symlinks are not allowed")
        if path.stat().st_size > max(1, int(max_bytes)):
            raise ValueError("artifact exceeds read limit")
        return path.read_bytes()
