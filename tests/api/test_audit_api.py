"""Tests for /v1/audit and /v1/audit/export endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

TENANT_ID = "tenant_test"


def _audit_row(
    action: str = "payment_intent.created",
    actor: str = "user@test.com",
) -> MagicMock:
    row = MagicMock()
    row.id = uuid.uuid4()
    row.tenant_id = TENANT_ID
    row.actor_sub = actor
    row.action = action
    row.target = "payment_intent:abc"
    row.detail = {"key": "value"}
    row.correlation_id = "corr-1"
    row.created_at = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    return row


def _setup_db_rows(mock_db: MagicMock, rows: list) -> None:
    mock_db.execute.return_value.scalars.return_value.all.return_value = rows


# ── GET /v1/audit ────────────────────────────────────────────────


def test_list_audit_logs(
    client: TestClient,
    auth_headers: dict,
    mock_db: MagicMock,
) -> None:
    _setup_db_rows(mock_db, [_audit_row(), _audit_row(action="auth.login.success")])

    resp = client.get("/v1/audit", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert len(body["items"]) == 2
    assert body["next_cursor"] is None


def test_list_audit_logs_with_pagination(
    client: TestClient,
    auth_headers: dict,
    mock_db: MagicMock,
) -> None:
    rows = [_audit_row() for _ in range(51)]
    _setup_db_rows(mock_db, rows)

    resp = client.get("/v1/audit?limit=50", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 50
    assert body["next_cursor"] is not None


def test_list_audit_with_action_filter(
    client: TestClient,
    auth_headers: dict,
    mock_db: MagicMock,
) -> None:
    _setup_db_rows(mock_db, [_audit_row(action="auth.login.success")])

    resp = client.get("/v1/audit?action=auth.login.success", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["action"] == "auth.login.success"


def test_list_audit_with_actor_filter(
    client: TestClient,
    auth_headers: dict,
    mock_db: MagicMock,
) -> None:
    _setup_db_rows(mock_db, [_audit_row(actor="admin@test.com")])

    resp = client.get("/v1/audit?actor_sub=admin@test.com", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["actor_sub"] == "admin@test.com"


def test_list_audit_empty(
    client: TestClient,
    auth_headers: dict,
    mock_db: MagicMock,
) -> None:
    _setup_db_rows(mock_db, [])

    resp = client.get("/v1/audit", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["next_cursor"] is None


# ── GET /v1/audit/export ─────────────────────────────────────────


def test_export_audit_logs(
    client: TestClient,
    auth_headers: dict,
    mock_db: MagicMock,
) -> None:
    _setup_db_rows(mock_db, [_audit_row()])

    resp = client.get("/v1/audit/export", headers=auth_headers)

    assert resp.status_code == 200
    assert "attachment" in resp.headers.get("content-disposition", "")
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1


def test_export_audit_empty(
    client: TestClient,
    auth_headers: dict,
    mock_db: MagicMock,
) -> None:
    _setup_db_rows(mock_db, [])

    resp = client.get("/v1/audit/export", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json() == []


# ── Auth enforcement ─────────────────────────────────────────────


def test_list_audit_without_auth_returns_401(client: TestClient) -> None:
    resp = client.get("/v1/audit", headers={"X-Tenant-Id": TENANT_ID})
    assert resp.status_code == 401


def test_export_without_auth_returns_401(client: TestClient) -> None:
    resp = client.get("/v1/audit/export", headers={"X-Tenant-Id": TENANT_ID})
    assert resp.status_code == 401
