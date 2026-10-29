"""Primitivo de lancamento no journal.

Todo lancamento contabil do servico passa por aqui. Centralizar isto resolve
tres problemas que estavam espalhados pelos modulos de dominio:

1. **Equilibrio por moeda.** A validacao antiga somava todas as linhas do
   lancamento, independentemente da moeda. Um lancamento com ``DEBIT 100 USD`` e
   ``CREDIT 100 BRL`` passava: a soma fechava em 100 = 100 enquanto o fato
   economico estava errado. Agora o equilibrio e verificado por moeda.

2. **Deduplicacao por fato.** O mesmo fato economico (liquidar o pagamento X)
   pode chegar por mais de um caminho ao mesmo tempo — o endpoint de captura e o
   webhook do provedor. A unicidade de ``(tenant_id, fact_key)`` no banco e o que
   garante lancamento unico; a verificacao previa por ``SELECT`` perdia a corrida.

3. **Correcao por compensacao.** ``reverse_entry`` cria um lancamento espelhado
   e vinculado ao original. O journal e append-only por trigger, portanto nao
   existe caminho para "ajustar" um saldo historico.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.application.exceptions import LedgerImbalanceError
from src.infrastructure.db.models import AccountConfig, LedgerEntry, LedgerLine
from src.shared.logging import get_logger
from src.shared.problem import http_problem

if TYPE_CHECKING:
    from collections.abc import Sequence

log = get_logger(__name__)

DEBIT = "DEBIT"
CREDIT = "CREDIT"

ENTRY_ORIGINAL = "ORIGINAL"
ENTRY_REVERSAL = "REVERSAL"
ENTRY_ADJUSTMENT = "ADJUSTMENT"


@dataclass(frozen=True)
class LineSpec:
    """Uma linha do lancamento, com valor sempre em ``Decimal``."""

    side: str
    account: str
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if self.side not in (DEBIT, CREDIT):
            raise ValueError(f"side invalido: {self.side}")
        if not isinstance(self.amount, Decimal):
            raise TypeError(
                f"amount de linha contabil deve ser Decimal, recebido {type(self.amount).__name__}"
            )
        if self.amount <= 0:
            raise ValueError(f"amount de linha contabil deve ser > 0, recebido {self.amount}")


def resolve_account(session: Session, tenant_id: str, code: str) -> str:
    cfg = session.execute(
        select(AccountConfig).where(
            AccountConfig.tenant_id == tenant_id,
            AccountConfig.code == code,
        )
    ).scalar_one_or_none()
    return cfg.code if cfg else code


def validate_balanced_by_currency(lines: Sequence[LineSpec], *, reference: str) -> None:
    """Exige debitos == creditos **dentro de cada moeda**.

    Soma zero no total nao e aprovacao contabil: um lancamento que debita 100 USD
    e credita 100 BRL soma zero e representa um fato impossivel.
    """
    if not lines:
        raise LedgerImbalanceError(reference, Decimal(0), Decimal(0))

    per_currency: dict[str, dict[str, Decimal]] = {}
    for line in lines:
        bucket = per_currency.setdefault(line.currency, {DEBIT: Decimal(0), CREDIT: Decimal(0)})
        bucket[line.side] += line.amount

    for currency, totals in sorted(per_currency.items()):
        if totals[DEBIT] != totals[CREDIT]:
            log.error(
                "ledger entry is not balanced for currency",
                extra={
                    "reference": reference,
                    "currency": currency,
                    "debits": str(totals[DEBIT]),
                    "credits": str(totals[CREDIT]),
                },
            )
            raise LedgerImbalanceError(f"{reference}[{currency}]", totals[DEBIT], totals[CREDIT])


def post_entry(
    session: Session,
    tenant_id: str,
    *,
    fact_key: str,
    lines: Sequence[LineSpec],
    payment_intent_id: object | None = None,
    entry_type: str = ENTRY_ORIGINAL,
    reverses_entry_id: object | None = None,
) -> LedgerEntry | None:
    """Grava um lancamento, uma unica vez por fato economico.

    Devolve o lancamento criado, ou ``None`` quando aquele fato ja estava
    lancado. O ``None`` nao e erro: e a resposta correta para a segunda chegada
    do mesmo evento, e o chamador deve trata-lo como sucesso idempotente.

    A decisao de gravar e tomada pelo banco, em uma unica instrucao
    ``INSERT ... ON CONFLICT DO NOTHING``. Uma verificacao previa por ``SELECT``
    nao resolveria: duas transacoes concorrentes nao se veem antes do commit, e
    ambas concluiriam que podem gravar.
    """
    validate_balanced_by_currency(lines, reference=fact_key)

    now = datetime.now(UTC)
    entry_id = uuid.uuid4()
    insert_entry = (
        pg_insert(LedgerEntry)
        .values(
            id=entry_id,
            tenant_id=tenant_id,
            payment_intent_id=payment_intent_id,
            entry_type=entry_type,
            reverses_entry_id=reverses_entry_id,
            fact_key=fact_key,
            posted_at=now,
        )
        .on_conflict_do_nothing(constraint="uq_ledger_entries_tenant_fact")
        .returning(LedgerEntry.id)
    )
    created_id = session.execute(insert_entry).scalar_one_or_none()

    if created_id is None:
        log.info(
            "ledger fact already posted; skipping",
            extra={"tenant_id": tenant_id, "fact_key": fact_key},
        )
        return None

    session.execute(
        insert(LedgerLine),
        [
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant_id,
                "entry_id": created_id,
                "side": line.side,
                "account": line.account,
                "amount": line.amount,
                "currency": line.currency,
            }
            for line in lines
        ],
    )

    entry = session.get(LedgerEntry, created_id)
    assert entry is not None  # noqa: S101 - acabamos de inserir nesta transacao
    log.info(
        "ledger entry posted",
        extra={
            "tenant_id": tenant_id,
            "fact_key": fact_key,
            "entry_id": str(created_id),
            "entry_type": entry_type,
            "lines": len(lines),
        },
    )
    return entry


def reverse_entry(
    session: Session,
    tenant_id: str,
    original: LedgerEntry,
    *,
    fact_key: str,
    amount: Decimal | None = None,
) -> LedgerEntry | None:
    """Cria o lancamento compensatorio de ``original``, vinculado a ele.

    Com ``amount`` o estorno e parcial e proporcional linha a linha; sem ele, a
    reversao e integral. O lancamento original permanece intacto: e o par
    original + reversao que conta a historia.
    """
    if not original.lines:
        raise http_problem(
            422,
            "Unprocessable Entity",
            f"cannot reverse entry {original.id}: it has no lines",
            instance="ledger",
        )

    original_total = sum(
        (Decimal(str(line.amount)) for line in original.lines if line.side == DEBIT),
        Decimal(0),
    )
    already_reversed = reversed_total(session, tenant_id, original)
    remaining = original_total - already_reversed
    if amount is not None:
        if amount <= 0:
            raise http_problem(
                422, "Unprocessable Entity", "reversal amount must be > 0", instance="ledger"
            )
        # O limite e cumulativo: a soma das reversoes nao pode passar do valor
        # lancado. Validar so a chamada atual permitiria 3 estornos de 40 sobre
        # um lancamento de 100.
        if amount > remaining:
            raise http_problem(
                422,
                "Unprocessable Entity",
                f"reversal amount {amount} exceeds remaining {remaining} "
                f"(entry total {original_total}, already reversed {already_reversed})",
                instance="ledger",
            )
    elif already_reversed > 0:
        raise http_problem(
            422,
            "Unprocessable Entity",
            f"entry {original.id} already has reversals totalling {already_reversed}; "
            "a full reversal would double-count",
            instance="ledger",
        )

    mirrored: list[LineSpec] = []
    for line in original.lines:
        line_amount = Decimal(str(line.amount))
        if amount is not None and original_total > 0:
            # Proporcional, com arredondamento explicito na menor unidade.
            line_amount = (line_amount * amount / original_total).quantize(Decimal("0.01"))
        if line_amount <= 0:
            continue
        mirrored.append(
            LineSpec(
                side=CREDIT if line.side == DEBIT else DEBIT,
                account=line.account,
                amount=line_amount,
                currency=line.currency,
            )
        )

    return post_entry(
        session,
        tenant_id,
        fact_key=fact_key,
        lines=mirrored,
        payment_intent_id=original.payment_intent_id,
        entry_type=ENTRY_REVERSAL,
        reverses_entry_id=original.id,
    )


def entry_total(entry: LedgerEntry, currency: str | None = None) -> Decimal:
    """Total debitado do lancamento, opcionalmente filtrado por moeda."""
    return sum(
        (
            Decimal(str(line.amount))
            for line in entry.lines
            if line.side == DEBIT and (currency is None or line.currency == currency)
        ),
        Decimal(0),
    )


def reversed_total(session: Session, tenant_id: str, entry: LedgerEntry) -> Decimal:
    """Quanto do lancamento ``entry`` ja foi revertido.

    Usado para impedir que estornos somados ultrapassem o valor original.
    """
    reversals = (
        session.execute(
            select(LedgerEntry).where(
                LedgerEntry.tenant_id == tenant_id,
                LedgerEntry.reverses_entry_id == entry.id,
            )
        )
        .scalars()
        .all()
    )
    return sum((entry_total(r) for r in reversals), Decimal(0))
