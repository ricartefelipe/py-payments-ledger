"""Fixtures para os testes que exigem PostgreSQL real.

Estes testes nao usam mock de sessao: as invariantes que eles provam (trigger
append-only, unicidade sob concorrencia, ``SELECT ... FOR UPDATE``) vivem no
banco e um mock nao as exercita. Quando o banco nao esta disponivel os testes
sao marcados como skip com a razao explicita, nunca silenciosamente ignorados.
"""

from __future__ import annotations

import base64
import os
import secrets
import uuid
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

# Chave fixa para o suite: os indices cegos precisam ser estaveis entre a
# escrita e a leitura dentro do mesmo teste.
TEST_ENCRYPTION_KEY = base64.b64encode(b"\x11" * 32).decode()

DEFAULT_TEST_DSN = "postgresql+psycopg://app:app@localhost:5434/app"


def _dsn() -> str:
    return os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL") or DEFAULT_TEST_DSN


def _unavailable(reason: str) -> None:
    """Skip localmente, falha onde a integracao e obrigatoria.

    Na CI (``REQUIRE_INTEGRATION=1``) um banco ausente nao pode virar skip: a
    suite aprovaria sem exercitar as invariantes que vivem no banco — trigger
    append-only, unicidade sob concorrencia, ``SELECT ... FOR UPDATE``. Na
    maquina de quem desenvolve sem os servicos de pe, o skip e util.
    """
    if os.getenv("REQUIRE_INTEGRATION") == "1":
        pytest.fail(f"integracao obrigatoria mas indisponivel: {reason}", pytrace=False)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def pg_engine() -> Generator[Engine, None, None]:
    dsn = _dsn()
    engine = create_engine(dsn, future=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - qualquer falha de conexao e tratada
        engine.dispose()
        _unavailable(f"PostgreSQL indisponivel em {dsn}: {exc}")
    # Garante que a migration de integridade financeira esta aplicada; sem ela
    # os testes abaixo testariam um schema que nao e o entregue.
    with engine.connect() as conn:
        missing = conn.execute(text("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'ledger_entries' AND column_name = 'fact_key'
                """)).first()
    if not missing:
        engine.dispose()
        _unavailable("schema sem a migration 0023; rode `alembic upgrade head`")
    yield engine
    engine.dispose()


@pytest.fixture
def pg_session(pg_engine: Engine) -> Generator[Session, None, None]:
    """Sessao transacional: o teste nunca deixa residuo no banco."""
    maker = sessionmaker(bind=pg_engine, future=True, expire_on_commit=False)
    session = maker()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def encryption_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Liga a cifra em repouso, como em producao.

    Varios defeitos deste servico so aparecem com ``ENCRYPTION_KEY`` definida:
    sem ela ``EncryptedString`` grava plaintext e a busca por igualdade funciona
    por acidente.
    """
    from src.shared.config import get_settings

    monkeypatch.setenv("ENCRYPTION_KEY", TEST_ENCRYPTION_KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def tenant_id(pg_session: Session) -> str:
    """Cria um tenant isolado e o remove no fim do teste."""
    from src.infrastructure.db.models import Tenant

    tid = f"t-{uuid.uuid4().hex[:12]}"
    pg_session.add(Tenant(id=tid, name=f"tenant {tid}"))
    pg_session.commit()
    yield tid
    # Limpeza restrita ao que este teste criou. ledger_* e append-only por
    # trigger, entao a remocao usa DELETE desabilitando o trigger apenas para
    # esta sessao de teste — nunca em producao.
    pg_session.rollback()
    pg_session.execute(text("SET session_replication_role = replica"))
    for table in (
        "ledger_lines",
        "ledger_entries",
        "gateway_operations",
        "outbox_events",
        "webhook_deliveries",
        "audit_log",
        "refunds",
        "payment_intents",
    ):
        pg_session.execute(
            text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": tid}  # noqa: S608
        )
    pg_session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tid})
    pg_session.execute(text("SET session_replication_role = DEFAULT"))
    pg_session.commit()


@pytest.fixture
def other_tenant_id(pg_session: Session) -> str:
    from src.infrastructure.db.models import Tenant

    tid = f"o-{uuid.uuid4().hex[:12]}"
    pg_session.add(Tenant(id=tid, name=f"tenant {tid}"))
    pg_session.commit()
    yield tid
    pg_session.rollback()
    pg_session.execute(text("SET session_replication_role = replica"))
    for table in ("ledger_lines", "ledger_entries", "payment_intents"):
        pg_session.execute(
            text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": tid}  # noqa: S608
        )
    pg_session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tid})
    pg_session.execute(text("SET session_replication_role = DEFAULT"))
    pg_session.commit()


def new_session(engine: Engine) -> Session:
    """Sessao independente, para simular um segundo worker/processo."""
    return sessionmaker(bind=engine, future=True, expire_on_commit=False)()


def random_ref(prefix: str = "pi") -> str:
    return f"{prefix}_{secrets.token_hex(8)}"
