# py-payments-ledger

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/fastapi-0.110+-00a393.svg)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/postgresql-16-336791.svg)](https://www.postgresql.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Motor de pagamentos com ledger contabil double-entry — arquitetura de producao em Python/FastAPI.

Implementa: **outbox pattern**, **idempotencia**, **RBAC/ABAC**, **rate limiting distribuido**, **observabilidade** (Prometheus/Grafana), **auditoria** e **integracao com orders** via RabbitMQ.

> Ver [ROADMAP.md](ROADMAP.md) para evolucao planejada.

---

## Inicio Rapido

```bash
# 1. Clonar e configurar
git clone https://github.com/union-solutions/py-payments-ledger.git && cd py-payments-ledger
cp .env.example .env

# 2. Subir tudo (Postgres, Redis, RabbitMQ, API, Worker, Prometheus, Grafana)
./scripts/up.sh

# 3. Executar migracoes
./scripts/migrate.sh

# 4. Popular dados de teste
./scripts/seed.sh

# 5. Validar com smoke tests
./scripts/smoke.sh
```

**URLs locais:**

| Servico | URL |
|---------|-----|
| API Swagger | http://localhost:8000/docs |
| RabbitMQ UI | http://localhost:15672 (guest/guest) |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin/admin) |

---

## Endpoints da API

### Auth

| Metodo | Path | Descricao |
|--------|------|-----------|
| POST | `/v1/auth/token` | Login e emissao de JWT |
| GET | `/v1/me` | Dados do usuario autenticado |

### Payments

| Metodo | Path | Descricao |
|--------|------|-----------|
| POST | `/v1/payment-intents` | Criar payment intent (aceita `Idempotency-Key`) |
| GET | `/v1/payment-intents/{id}` | Buscar payment intent |
| POST | `/v1/payment-intents/{id}/confirm` | Confirmar (requer `Idempotency-Key`) |

### Ledger

| Metodo | Path | Descricao |
|--------|------|-----------|
| GET | `/v1/ledger/entries` | Listar entradas do ledger (filtros: `from`, `to`) |
| GET | `/v1/ledger/balances` | Saldos agregados por conta (filtros: `from`, `to`) |

### Admin

| Metodo | Path | Descricao |
|--------|------|-----------|
| GET | `/v1/admin/chaos` | Obter config de chaos do tenant |
| PUT | `/v1/admin/chaos` | Configurar chaos do tenant |

### Infra

| Metodo | Path | Descricao |
|--------|------|-----------|
| GET | `/healthz` | Health check |
| GET | `/readyz` | Readiness (DB + Redis) |
| GET | `/metrics` | Prometheus metrics |
| GET | `/openapi.json` | OpenAPI spec |
| GET | `/docs` | Swagger UI |

---

## Credenciais de Teste

| Email | Senha | Tenant | Papel | Permissoes |
|-------|-------|--------|-------|------------|
| `admin@local` | `admin123` | global (*) | admin | Todas |
| `ops@demo` | `ops123` | tenant_demo | ops | payments:write/read, ledger:read |
| `sales@demo` | `sales123` | tenant_demo | sales | payments:read |

---

## Exemplos de curl

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

# Consultar balances
curl http://localhost:8000/v1/ledger/balances \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Id: tenant_demo"

# Consultar entradas do ledger com filtro de data
curl "http://localhost:8000/v1/ledger/entries?from=2026-01-01T00:00:00" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Id: tenant_demo"
```

---

## Variaveis de Ambiente

| Variavel | Default | Descricao |
|----------|---------|-----------|
| `DATABASE_URL` | `postgresql+psycopg://app:app@postgres:5432/app` | URL do PostgreSQL |
| `REDIS_URL` | `redis://redis:6379/0` | URL do Redis |
| `RABBITMQ_URL` | `amqp://guest:guest@rabbitmq:5672/` | URL do RabbitMQ |
| `JWT_SECRET` | `change-me` | Segredo JWT (trocar em producao!) |
| `JWT_ISSUER` | `local-auth` | Issuer do JWT |
| `TOKEN_EXPIRES_SECONDS` | `3600` | TTL do token |
| `RATE_LIMIT_WRITE_PER_MIN` | `60` | Rate limit escrita |
| `RATE_LIMIT_READ_PER_MIN` | `240` | Rate limit leitura |
| `IDEMPOTENCY_TTL_SECONDS` | `86400` | TTL da cache de idempotencia (24h) |
| `CHAOS_ENABLED` | `false` | Habilitar chaos middleware |
| `ORDERS_INTEGRATION_ENABLED` | `false` | Habilitar consumer de orders |
| `ORDERS_EXCHANGE` | `orders.x` | Exchange do servico de orders |
| `ORDERS_QUEUE` | `payments.orders.events` | Queue para eventos de orders |
| `ORDERS_ROUTING_KEY` | `order.confirmed` | Routing key para binding |

---

## Integracao com Orders (node-b2b-orders)

Quando `ORDERS_INTEGRATION_ENABLED=true`, o worker:

1. Consome eventos `order.confirmed` da exchange configurada
2. Cria um `PaymentIntent` com status `AUTHORIZED` (idempotente por `order_id`)
3. O outbox publica `payment.authorized`, o worker posta no ledger
4. O evento `payment.settled` e emitido com `customer_ref=order:<order_id>` para o Orders marcar o pedido como PAID

**Payload esperado de `order.confirmed`:**

```json
{
  "order_id": "uuid",
  "tenant_id": "tenant_demo",
  "total_amount": 150.00,
  "currency": "BRL",
  "customer_ref": "CUST-001",
  "correlation_id": "uuid"
}
```

---

## Arquitetura

```
┌─────────────────────────────────────────┐
│  API (FastAPI + Middlewares)             │  <- HTTP
├─────────────────────────────────────────┤
│  Application (Use Cases)                │  <- Logica de negocio
├─────────────────────────────────────────┤
│  Infrastructure                         │
│  ├─ PostgreSQL (state + outbox + audit) │
│  ├─ Redis (idempotencia, rate limit)    │
│  └─ RabbitMQ (event delivery + orders)  │
└─────────────────────────────────────────┘

Worker: outbox dispatcher + event consumer (payments + orders)
```

### Fluxo de pagamento

1. `POST /v1/payment-intents` → CREATED + outbox event
2. `POST /v1/payment-intents/{id}/confirm` → AUTHORIZED + outbox `payment.authorized`
3. Worker consome `payment.authorized` → posta ledger → SETTLED + outbox `payment.settled`

---

## Scripts

| Script | Descricao |
|--------|-----------|
| `./scripts/up.sh` | Sobe Docker Compose completo |
| `./scripts/down.sh` | Para e remove containers e volumes |
| `./scripts/migrate.sh` | Executa Alembic migrations |
| `./scripts/seed.sh` | Popula dados de teste |
| `./scripts/smoke.sh` | Smoke tests end-to-end |
| `./scripts/api-export.sh` | Exporta OpenAPI spec |
| `./scripts/logs.sh` | Tail de logs dos containers |

---

## Testes

```bash
python -m pytest tests/ -v
python -m pytest tests/unit/ -v
python -m pytest tests/ --cov=src --cov-report=html
```

---

## Troubleshooting

| Problema | Solucao |
|----------|---------|
| Connection refused (Redis/Postgres) | `./scripts/up.sh` para iniciar containers |
| 403 Forbidden | Verificar role do usuario e header `X-Tenant-Id` |
| 429 Too Many Requests | Aguardar 60s ou aumentar `RATE_LIMIT_*` |
| Outbox event stuck | Verificar RabbitMQ UI em http://localhost:15672 |
| Worker nao processa | `docker compose logs worker -f` para diagnosticar |

---

## Licenca

MIT License — veja [LICENSE](LICENSE) para detalhes.

Mantido por **Felipe Ricarte** (felipericartem@gmail.com) | **Union Solutions**
