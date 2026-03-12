"""Custom SQLAlchemy column types."""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from src.shared.config import get_settings
from src.shared.encryption import decrypt, encrypt


class EncryptedString(TypeDecorator[str]):
    """
    Transparently encrypts/decrypts string values at rest using AES-256-GCM.
    Uses ENCRYPTION_KEY from config. When key is not set, stores plaintext.
    Handles legacy unencrypted data on read.
    """

    impl = String
    cache_ok = True

    def __init__(self, length: int = 512, *args, **kwargs):
        # Encrypted base64 output is larger than plaintext
        super().__init__(length, *args, **kwargs)

    def process_bind_param(self, value: str | None, dialect):
        if value is None:
            return None
        key = get_settings().encryption_key
        return encrypt(value, key or None)

    def process_result_value(self, value: str | None, dialect):
        if value is None:
            return None
        key = get_settings().encryption_key
        return decrypt(value, key or None)
