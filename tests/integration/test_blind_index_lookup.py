"""Busca por referencia do provedor em coluna cifrada.

``EncryptedString`` cifra com AES-256-GCM usando um nonce aleatorio por escrita.
Isso significa que ``WHERE gateway_ref = :valor`` compara um ciphertext novo
contra o ciphertext armazenado e **nunca casa**. Antes do indice cego, com
``ENCRYPTION_KEY`` configurada (ou seja, em producao):

- o webhook do Stripe nao achava o PaymentIntent e devolvia 200 sem efeito;
- a reconciliacao classificava todo pagamento como ``MISSING_LOCAL``;
- a deduplicacao de pedido do consumer nao achava o intent anterior e criava
  um segundo pagamento para o mesmo pedido.

Os testes abaixo exercitam exatamente essas tres buscas com a cifra ligada.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.infrastructure.db.models import PaymentIntent
from src.shared.blind_index import blind_index_for
from tests.integration.conftest import random_ref


def _make_intent(session: Session, tenant_id: str, gateway_ref: str, customer_ref: str):
    pi = PaymentIntent(
        tenant_id=tenant_id,
        amount=Decimal("100.00"),
        currency="BRL",
        status="AUTHORIZED",
        customer_ref=customer_ref,
        gateway_ref=gateway_ref,
        gateway_provider="stripe",
    )
    session.add(pi)
    session.commit()
    return pi


def test_ciphertext_is_non_deterministic(encryption_on: None) -> None:
    """Base do problema: o mesmo texto cifra para valores diferentes."""
    from src.shared.config import get_settings
    from src.shared.encryption import encrypt

    key = get_settings().encryption_key
    assert key, "fixture encryption_on deve definir ENCRYPTION_KEY"
    first = encrypt("pi_abc123", key)
    second = encrypt("pi_abc123", key)
    assert first != second
    assert first.startswith("enc:v1:")


def test_blind_index_is_deterministic(encryption_on: None) -> None:
    """O indice cego, por outro lado, e estavel — e e isso que permite buscar."""
    assert blind_index_for("pi_abc123") == blind_index_for("pi_abc123")
    assert blind_index_for("pi_abc123") != blind_index_for("pi_abc124")
    # Nao e o plaintext nem derivavel dele sem a chave.
    digest = blind_index_for("pi_abc123")
    assert digest is not None
    assert "pi_abc123" not in digest
    assert len(digest) == 64


def test_equality_on_encrypted_column_never_matches(
    pg_session: Session, tenant_id: str, encryption_on: None
) -> None:
    """Documenta o defeito: a busca ingenua nao encontra a linha que existe."""
    ref = random_ref()
    _make_intent(pg_session, tenant_id, ref, "order:A-1")

    found = pg_session.execute(
        select(PaymentIntent).where(
            PaymentIntent.tenant_id == tenant_id,
            PaymentIntent.gateway_ref == ref,
        )
    ).scalar_one_or_none()

    assert found is None, (
        "se esta busca passar a funcionar, a cifra ficou deterministica "
        "e perdeu a protecao de nonce — revise EncryptedString"
    )


def test_lookup_by_blind_index_finds_the_intent(
    pg_session: Session, tenant_id: str, encryption_on: None
) -> None:
    """O caminho correto encontra a linha e devolve o plaintext decifrado."""
    ref = random_ref()
    created = _make_intent(pg_session, tenant_id, ref, "order:A-2")

    found = pg_session.execute(
        select(PaymentIntent).where(
            PaymentIntent.tenant_id == tenant_id,
            PaymentIntent.gateway_ref_bidx == blind_index_for(ref),
        )
    ).scalar_one_or_none()

    assert found is not None
    assert found.id == created.id
    assert found.gateway_ref == ref


def test_blind_index_is_filled_without_the_caller_setting_it(
    pg_session: Session, tenant_id: str, encryption_on: None
) -> None:
    """O listener de flush mantem o digest em sincronia automaticamente."""
    ref = random_ref()
    pi = _make_intent(pg_session, tenant_id, ref, "order:A-3")
    assert pi.gateway_ref_bidx == blind_index_for(ref)

    # Alterar so o valor cifrado deve recalcular o digest no flush.
    new_value = random_ref()
    pi.gateway_ref = new_value
    pg_session.commit()
    assert pi.gateway_ref_bidx == blind_index_for(new_value)

    found = pg_session.execute(
        select(PaymentIntent).where(
            PaymentIntent.gateway_ref_bidx == blind_index_for(new_value),
            PaymentIntent.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    assert found is not None and found.id == pi.id


def test_lookup_is_scoped_by_tenant(
    pg_session: Session, tenant_id: str, other_tenant_id: str, encryption_on: None
) -> None:
    """Referencia igual em dois tenants nao vaza de um para o outro.

    O caminho antigo buscava so por ``gateway_ref``, sem tenant: alem de nao
    achar nada com a cifra ligada, ``scalar_one_or_none`` levantaria
    ``MultipleResultsFound`` se dois tenants compartilhassem a referencia.
    """
    shared_ref = random_ref()
    mine = _make_intent(pg_session, tenant_id, shared_ref, "order:B-1")
    theirs = _make_intent(pg_session, other_tenant_id, shared_ref, "order:B-2")
    assert mine.id != theirs.id

    digest = blind_index_for(shared_ref)
    rows = (
        pg_session.execute(
            select(PaymentIntent).where(
                PaymentIntent.tenant_id == tenant_id,
                PaymentIntent.gateway_ref_bidx == digest,
            )
        )
        .scalars()
        .all()
    )
    assert [r.id for r in rows] == [mine.id]

    # Sem o escopo de tenant a mesma busca traz as duas linhas: e por isso que
    # o escopo nao e opcional.
    unscoped = (
        pg_session.execute(select(PaymentIntent).where(PaymentIntent.gateway_ref_bidx == digest))
        .scalars()
        .all()
    )
    assert len(unscoped) == 2


def test_order_dedupe_lookup_works_with_encryption_on(
    pg_session: Session, tenant_id: str, encryption_on: None
) -> None:
    """Deduplicacao de pedido do consumer: a chave e ``order:<id>`` em customer_ref.

    Com a cifra ligada e sem indice cego, a consulta de dedupe nao encontrava o
    intent anterior e cada reentrega do RabbitMQ gerava um pagamento novo.
    """
    order_id = uuid.uuid4().hex[:10]
    customer_ref = f"order:{order_id}"
    first = _make_intent(pg_session, tenant_id, random_ref(), customer_ref)

    existing = pg_session.execute(
        select(PaymentIntent).where(
            PaymentIntent.tenant_id == tenant_id,
            PaymentIntent.customer_ref_bidx == blind_index_for(customer_ref),
        )
    ).scalar_one_or_none()

    assert existing is not None, "a reentrega precisa reconhecer o pedido ja processado"
    assert existing.id == first.id
