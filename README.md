# py-payments-ledger

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/fastapi-0.110+-00a393.svg)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/postgresql-16-336791.svg)](https://www.postgresql.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Motor de **pagamentos** com **ledger contábil double-entry** em Python/FastAPI. Arquitetura de produção com **outbox pattern**, **idempotência**, **RBAC/ABAC**, **rate limiting distribuído**, **observabilidade** (Prometheus/Grafana), **auditoria** e integração com **node-b2b-orders** via RabbitMQ.

> Evolução planejada: [ROADMAP.md](ROADMAP.md)

---

## Índice

- [Visão geral](#visão-geral)
- [Quando usar](#quando-usar)
- [Quick Start](#quick-start-3-minutos)
- [URLs locais](#urls-locais)
- [Arquitetura](#arquitetura)
- [Fluxo de pagamento](#fluxo-de-pagamento)
- [Endpoints da API](#endpoints-da-api)
- [Eventos (integração com node-b2b-orders)](#eventos-integração-com-node-b2b-orders)
- [Segurança por ambiente](#segurança-por-ambiente)
- [Credenciais de teste](#credenciais-de-teste)
- [Exemplos curl](#exemplos-curl)
- [Variáveis de ambiente](#variáveis-de-ambiente)
- [E2E com Fluxe B2B Suite](#e2e-com-fluxe-b2b-suite)
- [Scripts](#scripts)
- [Qualidade e testes](#qualidade-e-testes)
- [Demonstração](#demonstração-3-minutos)
- [Troubleshooting](#troubleshooting)
- [Licença e mantenedor](#licença)

---

## Visão geral

O **py-payments-ledger** expõe uma API REST para **payment intents** (criação e confirmação) e um **ledger** double-entry para auditoria e saldos. Eventos são publicados via outbox para RabbitMQ; um worker processa o outbox e consome eventos de pedidos (`payment.charge_requested`) quando a integração com node-b2b-orders está habilitada.

**Principais capacidades:**

- **Payment intents**: criar (CREATED) e confirmar (AUTHORIZED → SETTLED) com idempotência (`Idempotency-Key`).
- **Ledger**: entradas e saldos por conta; integridade double-entry.
- **Outbox**: eventos `payment.authorized`, `payment.settled` persistidos e publicados pelo worker.
- **Integração orders**: consumir `payment.charge_requested` do worker de pedidos e publicar `payment.settled`.

---

## Quando usar

- Você precisa de um **motor de pagamentos** com ledger auditável em uma suíte B2B.
- Quer **idempotência** em criação e confirmação de payment intents.
- Deseja **integração assíncrona** com o serviço de pedidos (node-b2b-orders) via RabbitMQ.
- O JWT é emitido ou delegado por outro serviço (ex.: spring-saas-core); esta API **valida** e aplica RBAC/ABAC.

---

## Quick Start (3 minutos)

```bash
git clone https://github.com/ricartefelipe/py-payments-ledger.git
cd py-payments-ledger
cp .env.example .env

./scripts/up.sh
./scripts/seed.sh
./scripts/smoke.sh
```

`up.sh` sobe Docker Compose (PostgreSQL, Redis, RabbitMQ, API, worker) e aplica migrações. `smoke.sh` valida o fluxo completo.

---

## URLs locais

| Serviço | URL |
|---------|-----|
| API Swagger | http://localhost:8000/docs |
| RabbitMQ UI | http://localhost:15672 (guest/guest) |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin/admin) |

---

## Arquitetura

```
┌─────────────────────────────────────────┐
│  API (FastAPI + Middlewares)             │  ← HTTP
├─────────────────────────────────────────┤
│  Application (Use Cases)                 │  ← Lógica de negócio
├─────────────────────────────────────────┤
│  Infrastructure                         │
│  ├─ PostgreSQL (state + outbox + audit) │
│  ├─ Redis (idempotência, rate limit)    │
│  └─ RabbitMQ (event delivery + orders)  │
└─────────────────────────────────────────┘

Worker: outbox dispatcher + event consumer (payments + orders)
```

---

## Fluxo de pagamento

1. `POST /v1/payment-intents` → CREATED + outbox event.
2. `POST /v1/payment-intents/{id}/confirm` → AUTHORIZED + outbox `payment.authorized`.
3. Worker consome `payment.authorized` → posta ledger → SETTLED + outbox `payment.settled`.

---

## Endpoints da API

### Auth

| Método | Path | Descrição |
|--------|------|-----------|
| POST | `/v1/auth/token` | Login e emissão de JWT |
| GET | `/v1/me` | Dados do usuário autenticado |

### Payments (write requer Idempotency-Key)

| Método | Path | Descrição |
|--------|------|-----------|
| POST | `/v1/payment-intents` | Criar payment intent (**Idempotency-Key obrigatório**) |
| GET | `/v1/payment-intents/{id}` | Buscar payment intent |
| POST | `/v1/payment-intents/{id}/confirm` | Confirmar (**Idempotency-Key obrigatório**) |

### Ledger

| Método | Path | Descrição |
|--------|------|-----------|
| GET | `/v1/ledger/entries` | Listar entradas (filtros: from, to) |
| GET | `/v1/ledger/balances` | Saldos agregados por conta |

### Admin (local ou role admin)

| Método | Path | Descrição |
|--------|------|-----------|
| GET | `/v1/admin/chaos` | Obter config de chaos |
| PUT | `/v1/admin/chaos` | Configurar chaos |

### Infra

| Método | Path | Descrição |
|--------|------|-----------|
| GET | `/healthz` | Health check |
| GET | `/readyz` | Readiness (DB + Redis) |
| GET | `/metrics` | Prometheus metrics |
| GET | `/openapi.json` | OpenAPI spec |
| GET | `/docs` | Swagger UI |

---

## Eventos (integração com node-b2b-orders)

Contratos completos e exemplos: [docs/contracts/events.md](docs/contracts/events.md). [Prompt de evolução](docs/PROMPT-EVOLUCAO.md): objetivo entregável/vendável e IA/LLM. [Prompt de conclusão e vistoria](docs/PROMPT-CONCLUSAO-VISTORIA.md): critérios de qualidade, etapas finais e prontidão IA/LLM.

### Consumidos

- **payment.charge_requested** — publicado pelo orders worker (canônico).
- **order.confirmed** — legado, mantido para compatibilidade.

### Produzidos

- **payment.settled** — campos mínimos: `order_id`, `tenant_id`, `correlation_id`, `payment_intent_id`, `status`, `amount`, `currency`.

O worker aceita **camelCase e snake_case** nos payloads; o formato canônico documentado é snake_case.

---

## Segurança por ambiente

| Variável | Local | Produção |
|----------|--------|----------|
| CORS | `*` | Allowlist via `CORS_ORIGINS` |
| Chaos/Admin | Sempre disponível | Requer permissão `admin:write` |
| JWT_SECRET | Trocar em produção | Obrigatório |

---

## Credenciais de teste

| Email | Senha | Tenant | Papel | Permissões |
|-------|-------|--------|-------|------------|
| admin@local | admin123 | global (*) | admin | Todas |
| ops@demo | ops123 | tenant_demo | ops | payments:write/read, ledger:read |
| sales@demo | sales123 | tenant_demo | sales | payments:read |

---

## Exemplos curl

```bash
# Autenticar
TOKEN=$(curl -sS -X POST http://localhost:8000/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"email":"ops@demo","password":"ops123","tenantId":"tenant_demo"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Criar payment intent (idempotente)
curl -X POST http://localhost:8000/v1/payment-intents \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Id: tenant_demo" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{"amount": 100.00, "currency": "BRL", "customer_ref": "CUST-001"}'

# Confirmar payment intent
curl -X POST http://localhost:8000/v1/payment-intents/<PI_ID>/confirm \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Id: tenant_demo" \
  -H "Idempotency-Key: confirm-001"
```

---

## Variáveis de ambiente

| Variável | Default | Descrição |
|----------|---------|-----------|
| APP_ENV | local | Ambiente (local/staging/prod) |
| DATABASE_URL | postgresql+psycopg://... | PostgreSQL |
| REDIS_URL | redis://redis:6379/0 | Redis |
| RABBITMQ_URL | amqp://guest:guest@rabbitmq:5672/ | RabbitMQ |
| JWT_SECRET | change-me | **Trocar em produção** |
| CORS_ORIGINS | — | Produção: origens permitidas (vírgula) |
| ORDERS_INTEGRATION_ENABLED | false | Habilitar consumer de orders |
| ORDERS_ROUTING_KEYS | payment.charge_requested,order.confirmed | Routing keys |

Lista completa em `.env.example`.

---

## E2E com Fluxe B2B Suite

Para integração com fluxe-b2b-suite e spring-saas-core, o token é emitido pelo Core; esta API **valida** o JWT. Use o mesmo secret e issuer do Spring:

```bash
JWT_SECRET=local-dev-secret-min-32-chars-for-hs256-signing
JWT_ISSUER=spring-saas-core
ORDERS_INTEGRATION_ENABLED=true
```

Use o mesmo `RABBITMQ_URL` que o node-b2b-orders para receber `payment.charge_requested` e publicar `payment.settled`.

---

## Scripts

| Script | Descrição |
|--------|-----------|
| `./scripts/up.sh` | Sobe Docker Compose + migrate |
| `./scripts/down.sh` | Para e remove containers/volumes |
| `./scripts/migrate.sh` | Executa Alembic migrations |
| `./scripts/seed.sh` | Popula dados de teste |
| `./scripts/smoke.sh` | Smoke tests end-to-end |
| `./scripts/lint.sh` | ruff + black check + mypy |
| `./scripts/format.sh` | black + ruff --fix |

---

## Qualidade e testes

```bash
./scripts/lint.sh
./scripts/format.sh

# Ou diretamente:
python3 -m ruff check .
python3 -m black --check .
python3 -m mypy src

# Testes (com venv e deps dev instaladas)
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m pytest tests/ --cov=src --cov-report=html

# Ou com venv ativado (python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt -r requirements-dev.txt)
python -m pytest tests/unit -q
```

Para configurar o ambiente de desenvolvimento: `python3 -m venv .venv`, `source .venv/bin/activate` (ou `.venv\\Scripts\\activate` no Windows), depois `pip install -r requirements.txt -r requirements-dev.txt`. Em sistemas sem ensurepip: `apt install python3.12-venv` (Debian/Ubuntu) ou equivalente.

---

## Demonstração 3 minutos

Ver [docs/DEMO.md](docs/DEMO.md) — passos e o que mostrar.

---

## Troubleshooting

| Problema | Solução |
|----------|---------|
| Connection refused (Redis/Postgres) | `./scripts/up.sh` para iniciar containers |
| 403 Forbidden | Verificar role do usuário e header `X-Tenant-Id` |
| 429 Too Many Requests | Aguardar 60s ou aumentar `RATE_LIMIT_*` |
| Outbox event stuck | Verificar RabbitMQ UI em http://localhost:15672 |
| Worker não processa | `docker compose logs worker -f` |

---

## Licença

MIT License — [LICENSE](LICENSE).

**Mantido por:** Felipe Ricarte (felipericartem@gmail.com) | **Union Solutions**
