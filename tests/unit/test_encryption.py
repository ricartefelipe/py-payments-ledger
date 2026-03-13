"""Unit tests for encryption at rest."""

from __future__ import annotations

import base64
import secrets


from src.shared.encryption import decrypt, encrypt, generate_key, is_encryption_available


def _valid_key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


class TestEncryptDecrypt:
    def test_encrypt_decrypt_roundtrip(self) -> None:
        key = _valid_key()
        plain = "pi_3ABC123xyz"
        ct = encrypt(plain, key)
        assert ct != plain
        assert ct.startswith("enc:v1:")
        assert decrypt(ct, key) == plain

    def test_no_key_stores_plaintext(self) -> None:
        plain = "customer_cus_abc"
        ct = encrypt(plain, None)
        assert ct == plain
        assert decrypt(ct, None) == plain

    def test_empty_key_stores_plaintext(self) -> None:
        plain = "whsec_xyz"
        assert encrypt(plain, "") == plain
        assert decrypt(plain, "") == plain

    def test_legacy_plaintext_readable(self) -> None:
        key = _valid_key()
        plain = "pi_legacy_plain"
        assert decrypt(plain, key) == plain

    def test_encrypted_without_key_returns_ciphertext(self) -> None:
        key = _valid_key()
        plain = "secret"
        ct = encrypt(plain, key)
        result = decrypt(ct, None)
        assert result == ct


class TestGenerateKey:
    def test_generate_key_returns_base64_32bytes(self) -> None:
        k = generate_key()
        decoded = base64.b64decode(k)
        assert len(decoded) == 32
        assert k == base64.b64encode(decoded).decode("ascii")


class TestIsEncryptionAvailable:
    def test_valid_key_returns_true(self) -> None:
        assert is_encryption_available(_valid_key()) is True

    def test_empty_returns_false(self) -> None:
        assert is_encryption_available("") is False
        assert is_encryption_available(None) is False
