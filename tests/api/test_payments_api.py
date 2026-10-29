"""Tests for /v1/payment-intents endpoints."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

TENANT_ID = "tenant_test"

_SAMPLE_DTO_DATA = {
    "id": str(uuid.uuid4()),
    "amount": "100.00",
    "currency": "BRL",
    "status": "CREATED",
    "customer_ref": "CUST-1",
    "gateway_ref": None,
    "created_at": "2026-01-01T00:00:00+00:00",
    "updated_at": "2026-01-01T00:00:00+00:00",
}


def _dto():
    from src.application.payments import PaymentIntentDTO

    return PaymentIntentDTO(**_SAMPLE_DTO_DATA)


# ── POST /v1/payment-intents (create) ────────────────────────────


def test_create_payment_intent(
    client: TestClient,
    auth_headers: dict,
) -> None:
    with patch("src.api.routers.payments.create_payment_intent", return_value=_dto()):
        resp = client.post(
            "/v1/payment-intents",
            json={"amount": 100.0, "currency": "BRL", "customer_ref": "CUST-1"},
            headers={**auth_headers, "Idempotency-Key": "idem-001"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["currency"] == "BRL"
    assert body["status"] == "CREATED"


def test_create_payment_intent_missing_idempotency_key(
    client: TestClient,
    auth_headers: dict,
) -> None:
    resp = client.post(
        "/v1/payment-intents",
        json={"amount": 50.0, "currency": "BRL", "customer_ref": "CUST-2"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_create_payment_intent_idempotency_cache_hit(
    client: TestClient,
    auth_headers: dict,
    mock_redis: MagicMock,
) -> None:
    cached = {**_SAMPLE_DTO_DATA}
    mock_redis.get.return_value = json.dumps(cached)

    resp = client.post(
        "/v1/payment-intents",
        json={"amount": 100.0, "currency": "BRL", "customer_ref": "CUST-1"},
        headers={**auth_headers, "Idempotency-Key": "idem-dup"},
    )

    assert resp.status_code == 200
    assert resp.json()["id"] == cached["id"]


def test_create_payment_intent_validation_error(
    client: TestClient,
    auth_headers: dict,
) -> None:
    resp = client.post(
        "/v1/payment-intents",
        json={"amount": -5, "currency": "BRL", "customer_ref": "X"},
        headers={**auth_headers, "Idempotency-Key": "idem-bad"},
    )
    assert resp.status_code == 422


# ── GET /v1/payment-intents/{pid} ────────────────────────────────


def test_get_payment_intent(
    client: TestClient,
    auth_headers: dict,
) -> None:
    with patch("src.api.routers.payments.get_payment_intent", return_value=_dto()):
        resp = client.get(
            f"/v1/payment-intents/{_SAMPLE_DTO_DATA['id']}",
            headers=auth_headers,
        )

    assert resp.status_code == 200
    assert resp.json()["id"] == _SAMPLE_DTO_DATA["id"]


# ── POST /v1/payment-intents/{pid}/confirm ───────────────────────


def test_confirm_payment_intent(
    client: TestClient,
    auth_headers: dict,
) -> None:
    from src.application.payments import PaymentIntentDTO

    confirmed = PaymentIntentDTO(**{**_SAMPLE_DTO_DATA, "status": "AUTHORIZED"})

    with patch("src.api.routers.payments.confirm_payment_intent", return_value=confirmed):
        resp = client.post(
            f"/v1/payment-intents/{_SAMPLE_DTO_DATA['id']}/confirm",
            headers={**auth_headers, "Idempotency-Key": "idem-confirm-001"},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "AUTHORIZED"


def test_confirm_missing_idempotency_key(
    client: TestClient,
    auth_headers: dict,
) -> None:
    resp = client.post(
        f"/v1/payment-intents/{_SAMPLE_DTO_DATA['id']}/confirm",
        headers=auth_headers,
    )
    assert resp.status_code == 400


# ── Auth enforcement ─────────────────────────────────────────────


def test_create_without_auth_returns_401(client: TestClient) -> None:
    resp = client.post(
        "/v1/payment-intents",
        json={"amount": 50.0, "currency": "BRL", "customer_ref": "X"},
        headers={"Idempotency-Key": "k", "X-Tenant-Id": TENANT_ID},
    )
    assert resp.status_code == 401


def test_create_without_tenant_returns_400(
    client: TestClient,
    token_factory,
) -> None:
    token = token_factory()
    resp = client.post(
        "/v1/payment-intents",
        json={"amount": 50.0, "currency": "BRL", "customer_ref": "X"},
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "k"},
    )
    assert resp.status_code == 400


def test_validation_error_with_decimal_constraint_returns_422_not_500(
    client: TestClient,
    auth_headers: dict,
) -> None:
    """Erro de validacao em campo monetario deve ser 422, com corpo legivel.

    `amount` e `Decimal`, portanto a constraint violada coloca um `Decimal` no
    contexto do erro (`{"gt": Decimal("0")}`). Sem normalizar esse contexto, a
    serializacao do corpo falhava, o handler generico assumia e a resposta virava
    500: um erro do cliente aparecia como falha do servidor.
    """
    resp = client.post(
        "/v1/payment-intents",
        json={"amount": -5, "currency": "BRL", "customer_ref": "X"},
        headers={**auth_headers, "Idempotency-Key": "idem-decimal-ctx"},
    )

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["title"] == "Validation Error"
    # O corpo precisa ser JSON valido e citar o campo recusado.
    detail = body["detail"]
    assert any("amount" in str(item.get("loc", "")) for item in detail), detail


def test_request_model_accepts_an_exact_decimal_string() -> None:
    """Valor monetario pode chegar como string e e preservado exatamente.

    `float` nao representa 150.55 exatamente; `Decimal` sim. O teste afirma o
    contrato de entrada no proprio schema, sem depender do caminho completo.
    """
    from decimal import Decimal

    from src.api.routers.payments import CreatePaymentIntentRequest

    as_string = CreatePaymentIntentRequest(amount="150.55", currency="BRL", customer_ref="CUST-DEC")
    assert as_string.amount == Decimal("150.55")
    assert str(as_string.amount) == "150.55"

    # Numero JSON continua aceito: clientes atuais nao quebram.
    as_number = CreatePaymentIntentRequest(amount=150.55, currency="BRL", customer_ref="CUST-DEC")
    assert as_number.amount > Decimal("150.54")
