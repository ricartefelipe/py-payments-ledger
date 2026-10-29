#!/usr/bin/env python3
"""Recalcula os indices cegos das colunas cifradas.

Quando rodar
------------
O indice cego e derivado de ``ENCRYPTION_KEY`` via HKDF. Trocar essa chave
invalida **todos** os digests armazenados: o webhook do provedor, a
reconciliacao e a deduplicacao de pedido param de encontrar os pagamentos, em
silencio. Este script recalcula os digests com a chave ativa.

Rode:

* apos rotacionar ``ENCRYPTION_KEY`` (ver ``docs/operations.md``);
* apos restaurar um backup em um ambiente com chave diferente da origem;
* apos definir ``ENCRYPTION_KEY`` pela primeira vez em uma base que operava
  sem cifra.

Como rodar
----------
    DATABASE_URL=... ENCRYPTION_KEY=<chave nova> JWT_SECRET=... \\
      python3 scripts/reindex_blind_index.py --apply

``JWT_SECRET`` e exigido porque o script decifra os valores pelo ORM, que carrega
as mesmas settings do servico.

Sem ``--apply`` o script apenas relata quantas linhas estao fora de sincronia,
sem escrever nada.

Pre-requisitos
--------------
* A migration ``0023_financial_integrity`` precisa estar aplicada.
* ``ENCRYPTION_KEY`` deve ser a chave com a qual os valores foram **cifrados**:
  o script le o plaintext pelo ORM, portanto uma chave errada devolve o
  ciphertext em vez do valor e os digests sairiam errados. O script detecta esse
  caso e aborta.

Codigos de saida
----------------
0 — nada fora de sincronia, ou reindexacao concluida.
2 — ha linhas fora de sincronia (apenas em modo relatorio).
3 — pre-requisito ausente (schema, chave ou conexao).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# O script vive em scripts/ e importa `src`: coloca a raiz do repositorio no
# sys.path para funcionar de qualquer diretorio, inclusive com `python3 -I`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BATCH = 500


def _fail(message: str) -> None:
    print(f"[erro] {message}", file=sys.stderr)
    raise SystemExit(3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="escreve os digests recalculados (sem isto, apenas relata)",
    )
    parser.add_argument(
        "--batch", type=int, default=BATCH, help=f"linhas por commit (padrao {BATCH})"
    )
    args = parser.parse_args()

    if not os.getenv("DATABASE_URL"):
        _fail("DATABASE_URL nao definida")
    if not os.getenv("ENCRYPTION_KEY"):
        _fail(
            "ENCRYPTION_KEY nao definida. Sem ela o indice cego usa digest "
            "nao-chaveado e o resultado nao serve para a base cifrada."
        )

    # `EncryptedString` chama `get_settings()` ao decifrar, portanto a
    # configuracao precisa validar por inteiro — inclusive o segredo de JWT.
    # Falhar aqui com uma mensagem clara e melhor que um stack trace dentro do
    # carregamento de uma linha.
    try:
        from src.shared.config import get_settings

        get_settings()
    except Exception as exc:  # noqa: BLE001 - qualquer falha e pre-requisito
        _fail(
            f"configuracao invalida: {exc}. Defina tambem JWT_SECRET (ou "
            "JWKS_URI/JWT_PUBLIC_KEY); o script decifra pelo ORM e por isso "
            "carrega as mesmas settings do servico."
        )

    from sqlalchemy import create_engine, inspect, select
    from sqlalchemy.orm import Session

    from src.infrastructure.db.models import PaymentIntent, SavedPaymentMethod
    from src.shared.blind_index import blind_index_for
    from src.shared.encryption import _ENCRYPTED_PREFIX

    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    try:
        columns = {c["name"] for c in inspect(engine).get_columns("payment_intents")}
    except Exception as exc:  # noqa: BLE001 - qualquer falha de conexao e pre-requisito
        _fail(f"nao foi possivel inspecionar o schema: {exc}")
        return 3
    if "gateway_ref_bidx" not in columns:
        _fail("schema sem a migration 0023; rode `alembic upgrade head` primeiro")

    targets = (
        (
            PaymentIntent,
            (("gateway_ref", "gateway_ref_bidx"), ("customer_ref", "customer_ref_bidx")),
        ),
        (SavedPaymentMethod, (("gateway_token", "gateway_token_bidx"),)),
    )

    stale = 0
    updated = 0
    with Session(engine, future=True) as session:
        for model, pairs in targets:
            rows = session.execute(select(model)).scalars().all()
            for i, row in enumerate(rows, start=1):
                for source, target in pairs:
                    value = getattr(row, source, None)
                    if value is None:
                        continue
                    if isinstance(value, str) and value.startswith(_ENCRYPTED_PREFIX):
                        # O ORM devolveu ciphertext: a chave ativa nao decifra
                        # estes dados. Reindexar agora gravaria digests do
                        # ciphertext, que nao casam com nada.
                        _fail(
                            f"{model.__name__}.{source} nao foi decifrado com a "
                            "ENCRYPTION_KEY ativa; use a chave com que os dados "
                            "foram cifrados"
                        )
                    expected = blind_index_for(value)
                    if getattr(row, target, None) != expected:
                        stale += 1
                        if args.apply:
                            setattr(row, target, expected)
                            updated += 1
                if args.apply and i % args.batch == 0:
                    session.commit()
            if args.apply:
                session.commit()

    engine.dispose()

    if args.apply:
        print(f"reindexado: {updated} digest(s) atualizado(s)")
        return 0

    if stale:
        print(f"{stale} digest(s) fora de sincronia; rode de novo com --apply")
        return 2
    print("todos os digests estao em sincronia")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
