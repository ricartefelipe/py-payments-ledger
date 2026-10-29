from __future__ import annotations

import pika
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src.infrastructure.db.session import get_engine
from src.infrastructure.redis.client import get_redis
from src.shared.logging import get_logger

log = get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


def _unavailable(component: str) -> JSONResponse:
    """Readiness failure without echoing the driver message.

    A mensagem de erro do driver costuma conter host, porta, usuario e base de
    dados. O endpoint e nao autenticado, entao o detalhe fica apenas no log do
    servidor e a resposta diz somente qual componente falhou.
    """
    log.warning("readiness check failed", extra={"component": component}, exc_info=True)
    return JSONResponse(
        status_code=503,
        content={"status": "fail", "component": component},
    )


@router.get("/readyz")
def readyz(request: Request):
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - readiness reporta 503, nunca 500
        return _unavailable("db")

    try:
        get_redis().ping()
    except Exception:  # noqa: BLE001 - readiness reporta 503, nunca 500
        return _unavailable("redis")

    if request.app.state.settings.readiness_require_rabbit:
        try:
            rmq_url = request.app.state.settings.rabbitmq_url
            conn = pika.BlockingConnection(pika.URLParameters(rmq_url))
            conn.close()
        except Exception:  # noqa: BLE001 - readiness reporta 503, nunca 500
            return _unavailable("rabbitmq")

    return {"status": "ok"}
