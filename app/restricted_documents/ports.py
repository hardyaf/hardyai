from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EncryptedRestrictedValue:
    key_id: str
    algorithm: str
    nonce: bytes
    ciphertext: bytes
    authentication_tag: bytes


class RestrictedValueCipher(Protocol):
    """Authenticated envelope cipher supplied only by a reviewed local adapter."""

    provider_name: str

    def encrypt(self, *, plaintext: bytes, associated_data: bytes) -> EncryptedRestrictedValue: ...

    def decrypt(self, *, value: EncryptedRestrictedValue, associated_data: bytes) -> bytes: ...


class RestrictedValueStore(Protocol):
    """Encrypted value storage isolated from ordinary Documents persistence."""

    def put(self, *, value_ref: str, value: EncryptedRestrictedValue, operation_id: str) -> str: ...

    def get(self, *, value_ref: str) -> EncryptedRestrictedValue | None: ...
