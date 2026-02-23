# py-payments-ledger

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/fastapi-0.110+-00a393.svg)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/postgresql-16-336791.svg)](https://www.postgresql.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Motor de pagamentos com ledger contábil double-entry — arquitetura de produção em Python/FastAPI.

Implementa: **outbox pattern**, **idempotência**, **RBAC/ABAC**, **rate limiting distribuído**, **observabilidade** (Prometheus/Grafana), **auditoria** e **integração com orders** via RabbitMQ.

> Ver [ROADMAP.md](ROADMAP.md) para evolução planejada.

---

## Quick Start (3 minutos)

```bash
# 1. Clonar e configurar
git clone https://github.com/ricartefelipe/py-payments-ledger.git
cd py-payments-ledger
cp .env.example .env

# 2. Subir infra + API + Worker (inclui migrate)
./scripts/up.sh

# 3. Popular dados de teste
./scripts/seed.sh

# 4. Smoke tests (confirma fluxo completo)
./scripts/smoke.sh
```

**URLs locais:**

| Serviço      | URL                         |
|-------------|-----------------------------|
| API Swagger | http://localhost:8000/docs  |
| RabbitMQ UI | http://localhost:15672 (guest/guest) |
| Prometheus  | http://localhost:9090       |
| Grafana     | http://localhost:3000 (admin/admin) |

---

## Arquitetura

```
┌─────────────────────────────────────────┐
│  API (FastAPI + Middlewares)            │  ← HTTP
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

### Fluxo de pagamento

1. `POST /v1/payment-intents` → CREATED + outbox event
2. `POST /v1/payment-intents/{id}/confirm` → AUTHORIZED + outbox `payment.authorized`
3. Worker consome `payment.authorized` → posta ledger → SETTLED + outbox `payment.settled`

---

## Endpoints da API

### Auth

| Método | Path           | Descrição                  |
|--------|----------------|----------------------------|
| POST   | `/v1/auth/token` | Login e emissão de JWT    |
| GET    | `/v1/me`       | Dados do usuário autenticado |

### Payments (write requer `Idempotency-Key`)

| Método | Path                                 | Descrição                              |
|--------|--------------------------------------|----------------------------------------|
| POST   | `/v1/payment-intents`                | Criar payment intent (**requer Idempotency-Key**) |
| GET    | `/v1/payment-intents/{id}`           | Buscar payment intent                   |
| POST   | `/v1/payment-intents/{id}/confirm`   | Confirmar (**requer Idempotency-Key**) |

### Ledger

| Método | Path                   | Descrição                              |
|--------|------------------------|----------------------------------------|
| GET    | `/v1/ledger/entries`   | Listar entradas (filtros: `from`, `to`) |
| GET    | `/v1/ledger/balances` | Saldos agregados por conta             |

### Admin (apenas `APP_ENV=local` ou role admin)

| Método | Path           | Descrição                 |
|--------|----------------|---------------------------|
| GET    | `/v1/admin/chaos` | Obter config de chaos   |
| PUT    | `/v1/admin/chaos` | Configurar chaos        |

### Infra

| Método | Path           | Descrição              |
|--------|----------------|------------------------|
| GET    | `/healthz`     | Health check           |
| GET    | `/readyz`      | Readiness (DB + Redis)  |
| GET    | `/metrics`     | Prometheus metrics      |
| GET    | `/openapi.json`| OpenAPI spec            |
| GET    | `/docs`        | Swagger UI              |

---

## Eventos (Integração com node-b2b-orders)

Ver [docs/contracts/events.md](docs/contracts/events.md) para contratos completos e exemplos JSON.

### Consumidos

- **`payment.charge_requested`** — publicado pelo orders worker (canônico)
- **`order.confirmed`** — legado, mantido para compatibilidade

### Produzidos

- **`payment.settled`** — campos mínimos: `order_id`, `tenant_id`, `correlation_id`, `payment_intent_id`, `status`, `amount`, `currency`

O worker aceita **camelCase e snake_case** nos payloads; o formato canônico documentado é snake_case.

---

## Segurança por ambiente

| Variável      | Local                          | Produção                          |
|---------------|---------------------------------|-----------------------------------|
| CORS          | `*` (qualquer origem)          | Allowlist via `CORS_ORIGINS`      |
| Chaos/Admin   | Sempre disponível               | Requer permissão `admin:write`    |
| JWT_SECRET    | Trocar em produção             | Obrigatório                       |

---

## Credenciais de Teste

| Email       | Senha   | Tenant       | Papel | Permissões                              |
|-------------|---------|--------------|-------|-----------------------------------------|
| admin@local | admin123 | global (*)   | admin | Todas                                    |
| ops@demo    | ops123  | tenant_demo  | ops   | payments:write/read, ledger:read        |
| sales@demo  | sales123| tenant_demo  | sales | payments:read                            |

---

## Exemplos curl

```bash
# Autenticar
TOKEN=$(curl -sS -X POST http://localhost:8000/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"email":"ops@demo","password":"ops123","tenantId":"tenant_demo"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Criar payment intent (idempotente — Idempotency-Key obrigatório)
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

## Variáveis de Ambiente

| Variável                 | Default                          | Descrição                    |
|--------------------------|----------------------------------|------------------------------|
| `APP_ENV`                | `local`                          | Ambiente (local/staging/prod)|
| `DATABASE_URL`           | `postgresql+psycopg://...`       | URL do PostgreSQL            |
| `REDIS_URL`              | `redis://redis:6379/0`           | URL do Redis                  |
| `RABBITMQ_URL`           | `amqp://guest:guest@rabbitmq:5672/` | URL do RabbitMQ            |
| `JWT_SECRET`             | `change-me`                      | **Trocar em produção!**       |
| `CORS_ORIGINS`           | —                                | Produção: origens permitidas (vírgula) |
| `ORDERS_INTEGRATION_ENABLED` | `false`                     | Habilitar consumer de orders |
| `ORDERS_ROUTING_KEYS`    | `payment.charge_requested,order.confirmed` | Routing keys para binding |

Ver `.env.example` para lista completa.

---

## Demonstração 3 minutos

Ver [docs/DEMO.md](docs/DEMO.md) — o que rodar e o que mostrar.

---

## Scripts

| Script             | Descrição                          |
|--------------------|------------------------------------|
| `./scripts/up.sh`  | Sobe Docker Compose + migrate      |
| `./scripts/down.sh`| Para e remove containers/volumes   |
| `./scripts/migrate.sh` | Executa Alembic migrations     |
| `./scripts/seed.sh`| Popula dados de teste               |
| `./scripts/smoke.sh` | Smoke tests end-to-end           |
| `./scripts/lint.sh`| ruff + black check + mypy          |
| `./scripts/format.sh` | black + ruff --fix               |

---

## Qualidade e Testes

```bash
# Lint e formatação
./scripts/lint.sh
./scripts/format.sh

# Ou diretamente:
python3 -m ruff check .
python3 -m black --check .
python3 -m mypy src

# Testes
python3 -m pytest tests/ -v
python3 -m pytest tests/ --cov=src --cov-report=html
```

---

## Troubleshooting

| Problema                     | Solução                                                       |
|-----------------------------|----------------------------------------------------------------|
| Connection refused (Redis/Postgres) | `./scripts/up.sh` para iniciar containers                  |
| 403 Forbidden                | Verificar role do usuário e header `X-Tenant-Id`               |
| 429 Too Many Requests        | Aguardar 60s ou aumentar `RATE_LIMIT_*`                       |
| Outbox event stuck           | Verificar RabbitMQ UI em http://localhost:15672                |
| Worker não processa          | `docker compose logs worker -f` para diagnosticar              |

---

## Licença

MIT License — veja [LICENSE](LICENSE) para detalhes.

Mantido por **Felipe Ricarte** (felipericartem@gmail.com) | **Union Solutions**
