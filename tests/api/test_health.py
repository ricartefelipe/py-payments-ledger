"""Tests for /healthz and /readyz endpoints."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from tests.api.conftest import _test_settings


def test_healthz_returns_200(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_returns_ok_when_all_healthy(client: TestClient) -> None:
    mock_conn = MagicMock()
    mock_engine = MagicMock()
    mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

    with (
        patch("src.api.routers.health.get_engine", return_value=mock_engine),
        patch("src.api.routers.health.pika") as mock_pika,
    ):
        mock_pika.URLParameters.return_value = MagicMock()
        mock_pika.BlockingConnection.return_value = MagicMock()

        resp = client.get("/readyz")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_reports_db_failure(client: TestClient) -> None:
    with (
        patch(
            "src.api.routers.health.get_engine",
            side_effect=RuntimeError("db not available"),
        ),
    ):
        resp = client.get("/readyz")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "fail"
    assert body["component"] == "db"


def test_readyz_reports_redis_failure(client: TestClient, mock_redis: MagicMock) -> None:
    mock_conn = MagicMock()
    mock_engine = MagicMock()
    mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

    mock_redis.ping.side_effect = ConnectionError("redis down")

    with (patch("src.api.routers.health.get_engine", return_value=mock_engine),):
        resp = client.get("/readyz")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "fail"
    assert body["component"] == "redis"


def test_readyz_ok_without_rabbit_when_not_required() -> None:
    mock_conn = MagicMock()
    mock_engine = MagicMock()
    mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    mock_redis = MagicMock()
    mock_redis.ping.return_value = True

    settings = replace(_test_settings(), readiness_require_rabbit=False)

    with (
        patch("src.infrastructure.db.session.init_db"),
        patch("src.infrastructure.redis.client.init_redis"),
        patch("src.infrastructure.redis.client._client", mock_redis),
        patch("src.api.deps.auth.authorize"),
        patch("src.api.main.load_settings", return_value=settings),
        patch("src.api.routers.health.get_engine", return_value=mock_engine),
    ):
        from src.api.main import create_app

        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as tc:
            resp = tc.get("/readyz")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_reports_rabbitmq_failure(client: TestClient) -> None:
    mock_conn = MagicMock()
    mock_engine = MagicMock()
    mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

    with (
        patch("src.api.routers.health.get_engine", return_value=mock_engine),
        patch("src.api.routers.health.pika") as mock_pika,
    ):
        mock_pika.URLParameters.return_value = MagicMock()
        mock_pika.BlockingConnection.side_effect = ConnectionError("rabbitmq down")

        resp = client.get("/readyz")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "fail"
    assert body["component"] == "rabbitmq"
