# Configuração por ambiente — py-payments-ledger

Este projeto segue a mesma convenção de ambientes do **Fluxe B2B Suite**. Referência central: **fluxe-b2b-suite/config/env/README.md** (tabela de portas e hosts).

## Contextos

| Contexto | Config | Observação |
|----------|--------|------------|
| **docker** | `.env` copiado de `.env.example` | Hostnames `postgres`, `redis`, `rabbitmq` |
| **local** | `.env.local` copiado de `.env.local.example` | localhost:5435, 6382, 5675 (portas do host quando a infra sobe pelo docker-compose do spring-saas-core) |

O app carrega **`.env`** e depois **`.env.local`** (se existir); valores de `.env.local` prevalecem. Assim você mantém `.env` para Docker e usa `.env.local` para rodar na máquina sem trocar arquivos.

## Arquivos

- **`.env.example`** — Template com valores para **Docker** (compose).
- **`.env.local.example`** — Template com valores para rodar no **host** (localhost). Copie para `.env.local`.
- **`.env`** / **`.env.local`** — Gitignored; não commitar.

## Portas local (suite)

Postgres **5435**, Redis **6382**, RabbitMQ **5675**. Banco `app` (ou crie `payments` no mesmo Postgres se quiser separar). Ver **fluxe-b2b-suite/config/env/portas-local.md**.

## Rodar local

```bash
cp .env.local.example .env.local
# opcional: editar .env.local se suas portas forem outras
uvicorn src.api.main:create_app --factory --host 0.0.0.0 --port 8000
```

Worker:

```bash
python -m src.worker.main
```

(Com `.env.local` no mesmo diretório, o módulo `config` carrega `.env` e `.env.local` ao ser importado.)
