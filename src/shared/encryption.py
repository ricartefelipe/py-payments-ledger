"""Encryption at rest for sensitive payment data using AES-256-GCM."""

from __future__ import annotations

import base64
import logging
import secrets
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger(__name__)

# Prefix to identify encrypted values (enables reading unencrypted legacy data)
_ENCRYPTED_PREFIX = "enc:v1:"


def _get_aesgcm(key_b64: Optional[str]):
    """Create AESGCM instance from base64 key, or None if key not set."""
    if not key_b64 or not key_b64.strip():
        return None
    try:
        key = base64.b64decode(key_b64.strip())
        if len(key) != 32:
            log.warning("ENCRYPTION_KEY must be 32 bytes (base64-decoded), got %d", len(key))
            return None
        return AESGCM(key)
    except Exception as e:
        log.warning("Invalid ENCRYPTION_KEY: %s", e)
        return None


def encrypt(plaintext: str, key_b64: Optional[str]) -> str:
    """Encrypt plaintext with AES-256-GCM. Returns prefix + base64(nonce+ciphertext+tag)."""
    cipher = _get_aesgcm(key_b64)
    if cipher is None:
        log.debug("ENCRYPTION_KEY not set, storing plaintext")
        return plaintext

    nonce = secrets.token_bytes(12)
    plainbytes = plaintext.encode("utf-8")
    ct_and_tag = cipher.encrypt(nonce, plainbytes, None)
    combined = nonce + ct_and_tag
    return _ENCRYPTED_PREFIX + base64.b64encode(combined).decode("ascii")


def decrypt(ciphertext: str, key_b64: Optional[str]) -> str:
    """
    Decrypt ciphertext. If value is not encrypted (no prefix) or key not set,
    returns the value as-is (for backward compatibility with unencrypted data).
    """
    if not ciphertext or not ciphertext.startswith(_ENCRYPTED_PREFIX):
        return ciphertext

    cipher = _get_aesgcm(key_b64)
    if cipher is None:
        log.warning("Encrypted value present but ENCRYPTION_KEY not set, returning ciphertext")
        return ciphertext

    try:
        payload = base64.b64decode(ciphertext[len(_ENCRYPTED_PREFIX) :])
        nonce, ct_and_tag = payload[:12], payload[12:]
        plainbytes = cipher.decrypt(nonce, ct_and_tag, None)
        return plainbytes.decode("utf-8")
    except Exception as e:
        log.warning("Decryption failed: %s", e)
        return ciphertext


def generate_key() -> str:
    """Generate a random 32-byte key, base64-encoded for ENCRYPTION_KEY."""
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def is_encryption_available(key_b64: Optional[str]) -> bool:
    """Return True if encryption is enabled (valid key set)."""
    return _get_aesgcm(key_b64) is not None


def main() -> None:
    """CLI entry point: prints a new base64-encoded 32-byte key for ENCRYPTION_KEY."""
    print(generate_key())  # noqa: T201


if __name__ == "__main__":
    main()
