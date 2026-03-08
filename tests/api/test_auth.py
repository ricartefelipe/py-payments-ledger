"""Tests for /v1/auth/token and /v1/me endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

TENANT_ID = "tenant_test"


def test_token_with_valid_credentials(client: TestClient, token_factory) -> None:
    fake_token = token_factory()
    result = MagicMock()
    result.access_token = fake_token
    result.token_type = "Bearer"
    result.expires_in = 3600

    with patch(
        "src.api.routers.auth.authenticate_and_issue_token",
        return_value=result,
    ):
        resp = client.post(
            "/v1/auth/token",
            json={"email": "user@test.com", "password": "secret123"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] == fake_token
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600


def test_token_with_invalid_credentials(client: TestClient) -> None:
    with patch(
        "src.api.routers.auth.authenticate_and_issue_token",
        side_effect=HTTPException(
            status_code=401,
            detail={"title": "Unauthorized", "detail": "Invalid credentials"},
        ),
    ):
        resp = client.post(
            "/v1/auth/token",
            json={"email": "bad@test.com", "password": "wrong"},
        )

    assert resp.status_code == 401


def test_token_missing_email_returns_422(client: TestClient) -> None:
    resp = client.post("/v1/auth/token", json={"password": "x"})
    assert resp.status_code == 422


def test_me_with_valid_token(client: TestClient, token_factory) -> None:
    token = token_factory(
        sub="user@test.com",
        tid=TENANT_ID,
        roles=["operator"],
        perms=["payments:read"],
    )
    resp = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["sub"] == "user@test.com"
    assert body["tid"] == TENANT_ID
    assert "operator" in body["roles"]


def test_me_without_token_returns_401(client: TestClient) -> None:
    resp = client.get("/v1/me")
    assert resp.status_code == 401


def test_me_with_malformed_token_returns_401(client: TestClient) -> None:
    resp = client.get("/v1/me", headers={"Authorization": "Bearer invalid.jwt.token"})
    assert resp.status_code == 401
