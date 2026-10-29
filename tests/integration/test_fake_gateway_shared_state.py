"""O gateway simulado precisa compartilhar estado entre processos.

A API autoriza e o **worker** captura: são containers distintos. O estado do
adaptador fake vivia em um dicionário do processo, então a captura encontrava
`not_found` para uma autorização que existia — e o pagamento nunca liquidava.
O singleton do factory não resolve isso: ele compartilha a instância dentro de
um processo, não entre processos.

Este arquivo foi criado porque o smoke contra a imagem revelou o problema que a
suíte não cobria: todos os testes do fake rodavam em um único processo.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

from src.application.ports.payment_gateway import GatewayStatus
from src.infrastructure.gateway.fake import FakeGatewayAdapter


@pytest.fixture
def redis_backed(monkeypatch: pytest.MonkeyPatch):
    """Inicializa o cliente Redis real; skip quando indisponível."""
    from redis import Redis

    from src.infrastructure.redis import client as redis_client

    url = os.getenv("TEST_REDIS_URL") or os.getenv("REDIS_URL") or "redis://localhost:6381/0"
    try:
        conn = Redis.from_url(url, decode_responses=True)
        conn.ping()
    except Exception as exc:  # noqa: BLE001 - ausência de Redis é pré-requisito
        if os.getenv("REQUIRE_INTEGRATION") == "1":
            pytest.fail(f"Redis obrigatório mas indisponível em {url}: {exc}", pytrace=False)
        pytest.skip(f"Redis indisponível em {url}: {exc}")
    monkeypatch.setattr(redis_client, "_client", conn, raising=False)
    yield conn
    for key in conn.scan_iter(match="fakegw:ref:*"):
        conn.delete(key)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_capture_in_another_process_finds_the_authorization(redis_backed) -> None:
    """Duas instâncias independentes representam API e worker."""
    api_side = FakeGatewayAdapter()
    worker_side = FakeGatewayAdapter()

    authorized = _run(api_side.authorize("t1", Decimal("120.00"), "BRL", "CUST-1", "idem-1"))
    assert authorized.success
    ref = authorized.gateway_ref

    captured = _run(worker_side.capture(ref, Decimal("120.00"), "BRL", "cap-idem-1"))

    assert captured.success is True, "a captura no 'outro processo' não encontrou a autorização"
    assert captured.status == GatewayStatus.CAPTURED


def test_capture_still_reports_not_found_for_an_unknown_reference(redis_backed) -> None:
    """A tolerância não pode virar 'aceita qualquer referência'."""
    adapter = FakeGatewayAdapter()
    result = _run(adapter.capture("gw_does_not_exist", Decimal("10.00"), "BRL", "x"))

    assert result.success is False
    assert result.status == GatewayStatus.NOT_FOUND
    assert result.error_code == "not_found"


def test_status_is_visible_across_instances(redis_backed) -> None:
    api_side = FakeGatewayAdapter()
    worker_side = FakeGatewayAdapter()

    authorized = _run(api_side.authorize("t1", Decimal("50.00"), "BRL", "C", "idem-2"))
    ref = authorized.gateway_ref
    _run(worker_side.capture(ref, Decimal("50.00"), "BRL", "cap-2"))

    # A terceira instância vê o resultado das duas primeiras.
    probe = FakeGatewayAdapter()
    status = _run(probe.get_status(ref))
    assert status.success is True
    assert status.status == GatewayStatus.CAPTURED


def test_without_redis_the_store_stays_process_local() -> None:
    """Sem Redis o comportamento anterior é preservado (testes unitários)."""
    from src.infrastructure.redis import client as redis_client

    original = redis_client._client
    redis_client._client = None
    try:
        api_side = FakeGatewayAdapter()
        worker_side = FakeGatewayAdapter()
        authorized = _run(api_side.authorize("t1", Decimal("10.00"), "BRL", "C", "idem-3"))
        # Instâncias distintas não se veem: é a limitação conhecida, e é por isso
        # que o Redis existe no caminho de verdade.
        missing = _run(worker_side.capture(authorized.gateway_ref, Decimal("10.00"), "BRL", "c"))
        assert missing.status == GatewayStatus.NOT_FOUND
        # A mesma instância, porém, captura normalmente.
        same = _run(api_side.capture(authorized.gateway_ref, Decimal("10.00"), "BRL", "c"))
        assert same.success is True
    finally:
        redis_client._client = original
