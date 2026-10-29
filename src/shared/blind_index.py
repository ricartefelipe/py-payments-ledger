"""Deterministic lookup keys for columns that are encrypted at rest.

``EncryptedString`` uses AES-256-GCM with a fresh random nonce per write, so the
same plaintext never produces the same ciphertext.  That is the correct property
for confidentiality, but it makes ``WHERE encrypted_column = :value`` impossible:
the bind parameter is encrypted with a different nonce than the stored row, so
the comparison never matches.

A blind index solves this without weakening encryption at rest: alongside the
encrypted column we store a keyed, deterministic digest of the plaintext and
search on that instead.  An attacker reading the database sees only the digest,
and without the key cannot derive the plaintext or confirm a guess.

The index key is derived from ``ENCRYPTION_KEY`` with HKDF-SHA256 so that no new
mandatory secret is introduced.  **Rotating ``ENCRYPTION_KEY`` therefore also
invalidates every stored blind index** — see ``scripts/reindex_blind_index.py``
and the rotation section in ``docs/operations.md``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from src.shared.logging import get_logger

log = get_logger(__name__)

_HKDF_INFO = b"py-payments-ledger/blind-index/v1"

# Hex digest truncated to 32 bytes of entropy: collision-resistant for lookup
# keys while keeping the column narrow enough to index cheaply.
DIGEST_LENGTH = 64


def _derive_key(key_b64: str | None) -> bytes | None:
    """Derive the blind-index key from the at-rest encryption key."""
    if not key_b64 or not key_b64.strip():
        return None
    try:
        master = base64.b64decode(key_b64.strip())
    except (binascii.Error, ValueError):
        log.warning("ENCRYPTION_KEY is not valid base64; blind index falls back to unkeyed digest")
        return None
    if len(master) != 32:
        log.warning("ENCRYPTION_KEY must decode to 32 bytes; blind index falls back to unkeyed")
        return None
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_HKDF_INFO,
    ).derive(master)


def normalize(value: str) -> str:
    """Normalize a value before digesting.

    Gateway references and order references are case-sensitive opaque tokens, so
    only surrounding whitespace is stripped.  Normalizing more aggressively would
    make two distinct provider references collide onto one index entry.
    """
    return value.strip()


def blind_index(value: str | None, key_b64: str | None) -> str | None:
    """Return the deterministic lookup digest for ``value``.

    With a key configured the digest is ``HMAC-SHA256``; without one it is a
    plain SHA-256.  Both are deterministic, which is what the lookup requires.
    The unkeyed form is only reachable when ``ENCRYPTION_KEY`` is unset, in which
    case ``EncryptedString`` already stores plaintext, so the digest reveals
    nothing that the row itself does not.
    """
    if value is None:
        return None
    material = normalize(value).encode("utf-8")
    key = _derive_key(key_b64)
    if key is None:
        return hashlib.sha256(material).hexdigest()
    return hmac.new(key, material, hashlib.sha256).hexdigest()


def blind_index_for(value: str | None) -> str | None:
    """``blind_index`` using the key from the active settings."""
    from src.shared.config import get_settings

    return blind_index(value, get_settings().encryption_key or None)
