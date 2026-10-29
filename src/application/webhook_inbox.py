"""Inbox de eventos de provedor: registra, deduplica e processa uma unica vez.

O que existia antes: a assinatura era verificada corretamente sobre os bytes
originais, e em seguida o evento era processado — sem nenhum registro do
``event_id``. Consequencias:

* **Reentrega.** Provedores entregam pelo menos uma vez e reentregam quando a
  resposta demora. Cada reentrega de ``charge.refunded`` aplicava outro estorno.
* **Concorrencia.** Duas entregas simultaneas do mesmo evento eram processadas
  em paralelo, sem nada que as serializasse.
* **Falha engolida.** Handlers capturavam ``Exception`` e devolviam 200, de modo
  que o provedor marcava a entrega como bem-sucedida e nunca reenviava: o evento
  era perdido em silencio.

Aqui, o registro do evento e o seu efeito acontecem na **mesma transacao**. Ou os
dois persistem, ou nenhum dos dois — e nesse caso a resposta e um erro, para o
provedor reenviar. A deduplicacao e feita pela unicidade
``(provider, event_id)``, aplicada pelo banco: a segunda entrega colide e e
reconhecida sem repetir o efeito.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.infrastructure.db.models import WebhookInbox
from src.shared.logging import get_logger

log = get_logger(__name__)

#: Entregas registradas mas nao concluidas por mais tempo que isto indicam um
#: processo que caiu no meio. Ficam visiveis na fila de recuperacao.
STALE_AFTER = timedelta(minutes=15)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Reception:
    """Resultado do registro de uma entrega."""

    #: ``False`` quando este evento ja havia sido registrado antes.
    is_new: bool
    inbox_id: Any | None
    previous_status: str | None = None


def register(
    session: Session,
    *,
    provider: str,
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
    tenant_id: str | None = None,
) -> Reception:
    """Registra a entrega, deduplicando pelo banco.

    Deve ser chamado **dentro** da mesma transacao que aplica o efeito. Se o
    evento ja estava registrado, devolve ``is_new=False`` e o chamador responde
    sucesso sem reprocessar.

    Uma entrega concorrente do mesmo evento bloqueia aqui ate a primeira
    transacao terminar, e depois recebe ``is_new=False``. E essa serializacao
    pelo indice unico que impede o lancamento duplo.
    """
    stmt = (
        pg_insert(WebhookInbox)
        .values(
            provider=provider,
            event_id=event_id,
            event_type=event_type,
            tenant_id=tenant_id,
            payload=payload,
            status=WebhookInbox.RECEIVED,
            attempts=1,
            received_at=_utcnow(),
        )
        .on_conflict_do_nothing(constraint="uq_webhook_inbox_provider_event")
        .returning(WebhookInbox.id)
    )
    new_id = session.execute(stmt).scalar_one_or_none()

    if new_id is not None:
        log.info(
            "webhook event registered",
            extra={"provider": provider, "event_id": event_id, "event_type": event_type},
        )
        return Reception(is_new=True, inbox_id=new_id)

    existing = session.execute(
        select(WebhookInbox).where(
            WebhookInbox.provider == provider,
            WebhookInbox.event_id == event_id,
        )
    ).scalar_one_or_none()
    previous_status = existing.status if existing else None
    if existing is not None:
        existing.attempts += 1
    log.info(
        "duplicate webhook delivery; not processing again",
        extra={
            "provider": provider,
            "event_id": event_id,
            "previous_status": previous_status or "",
        },
    )
    return Reception(
        is_new=False,
        inbox_id=existing.id if existing else None,
        previous_status=previous_status,
    )


def mark_processed(
    session: Session, inbox_id: Any, *, tenant_id: str | None = None, note: str = ""
) -> None:
    entry = session.get(WebhookInbox, inbox_id)
    if entry is None:  # pragma: no cover
        return
    entry.status = WebhookInbox.PROCESSED
    entry.processed_at = _utcnow()
    entry.error = note[:1024] if note else None
    if tenant_id and not entry.tenant_id:
        entry.tenant_id = tenant_id


def mark_ignored(session: Session, inbox_id: Any, reason: str) -> None:
    """Evento valido que nao se aplica a este servico (ou sem correspondencia).

    Fica registrado — e nao reprocessado — para que a reconciliacao possa
    investigar por que um evento assinado nao encontrou destino local.
    """
    entry = session.get(WebhookInbox, inbox_id)
    if entry is None:  # pragma: no cover
        return
    entry.status = WebhookInbox.IGNORED
    entry.processed_at = _utcnow()
    entry.error = reason[:1024]


def mark_failed(session: Session, inbox_id: Any, error: str) -> None:
    """Registra falha para reentrega.

    Chamado em transacao propria: a transacao do efeito ja foi desfeita.
    """
    entry = session.get(WebhookInbox, inbox_id)
    if entry is None:  # pragma: no cover
        return
    entry.status = WebhookInbox.FAILED
    entry.error = error[:1024]


def list_stale(
    session: Session, *, provider: str | None = None, limit: int = 100
) -> list[WebhookInbox]:
    """Entregas presas em ``RECEIVED``: o processo caiu depois do registro.

    Com registro e efeito na mesma transacao isto nao deveria ocorrer, porque o
    rollback desfaz os dois. A fila existe como rede de seguranca e para tornar
    o caso visivel em vez de invisivel.
    """
    cutoff = _utcnow() - STALE_AFTER
    q = select(WebhookInbox).where(
        WebhookInbox.status.in_([WebhookInbox.RECEIVED, WebhookInbox.FAILED]),
        WebhookInbox.received_at < cutoff,
    )
    if provider:
        q = q.where(WebhookInbox.provider == provider)
    return list(
        session.execute(q.order_by(WebhookInbox.received_at.asc()).limit(limit)).scalars().all()
    )


def already_processed(session: Session, *, provider: str, event_id: str) -> bool:
    entry = session.execute(
        select(WebhookInbox).where(
            WebhookInbox.provider == provider,
            WebhookInbox.event_id == event_id,
        )
    ).scalar_one_or_none()
    return entry is not None and entry.status in (WebhookInbox.PROCESSED, WebhookInbox.IGNORED)


def apply_provider_status(
    session: Session,
    *,
    provider: str,
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
    gateway_ref: str,
    target_status: str,
    amount: Any | None = None,
    currency: str | None = None,
    event_ref: str | None = None,
) -> str:
    """Registra a entrega e aplica a mudanca de estado, na mesma transacao.

    Compartilhado por PagSeguro e Mercado Pago, que tinham a mesma estrutura:
    processar sem registrar o evento, e engolir qualquer excecao devolvendo
    ``200`` — o que faz o provedor considerar a entrega concluida e nunca
    reenviar, perdendo o evento em silencio.

    Devolve ``"duplicate"``, ``"applied"``, ``"already_applied"`` ou
    ``"ignored"``. Excecoes sobem: quem chama deve desfazer a transacao e
    responder erro, para o provedor reenviar.
    """
    from src.application.payments import update_payment_from_stripe_event

    reception = register(
        session,
        provider=provider,
        event_id=event_id,
        event_type=event_type,
        payload=payload,
    )
    if not reception.is_new:
        if session.in_transaction():
            session.commit()
        return "duplicate"

    changed = update_payment_from_stripe_event(
        session,
        gateway_ref,
        target_status,
        provider=provider,
        amount=amount,
        currency=currency,
        event_ref=event_ref,
    )
    if changed:
        mark_processed(session, reception.inbox_id)
        result = "applied"
    else:
        mark_ignored(
            session,
            reception.inbox_id,
            f"no change applied for {target_status} (no local payment or already applied)",
        )
        result = "already_applied"
    if session.in_transaction():
        session.commit()
    return result
