from __future__ import annotations

import pika
from fastapi import APIRouter, Request
from sqlalchemy import text

from src.infrastructure.db.session import get_engine
from src.infrastructure.redis.client import get_redis

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/readyz")
def readyz(request: Request):
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        return {"status": "fail", "component": "db", "error": str(e)}
    try:
        get_redis().ping()
    except Exception as e:
        return {"status": "fail", "component": "redis", "error": str(e)}
    try:
        rmq_url = request.app.state.settings.rabbitmq_url
        conn = pika.BlockingConnection(pika.URLParameters(rmq_url))
        conn.close()
    except Exception as e:
        return {"status": "fail", "component": "rabbitmq", "error": str(e)}
    return {"status": "ok"}
