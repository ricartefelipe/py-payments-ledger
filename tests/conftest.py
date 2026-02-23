"""Pytest configuration and fixtures."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _env_local() -> None:
    os.environ.setdefault("APP_ENV", "local")
    os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://app:app@localhost:5432/app")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
    os.environ.setdefault("JWT_SECRET", "test-secret")
    os.environ.setdefault("JWT_ISSUER", "test-issuer")
