# py-payments-ledger

**Motor de pagamentos com ledger contábil double-entry** — demonstração de padrões de produção em Python/FastAPI.

Implementa: **outbox pattern**, **idempotência**, **RBAC/ABAC**, **rate limiting distribuído**, **observabilidade** (Prometheus/Grafana) e **processamento assíncrono** via RabbitMQ.

---

## 🎯 Características principais

- **Padrão Outbox**: estado + eventos salvos atomicamente no banco; worker consome e publica para RabbitMQ
- **At-least-once delivery**: garantia com DLQ (dead-letter queue)
- **Idempotência**: via `Idempotency-Key` armazenado em Redis
- **Multi-tenant**: `X-Tenant-Id` header com isolamento de dados
- **Autenticação**: JWT HS256 com claims personalizados (tid, roles, plan, region)
- **Autorização**: RBAC (role-based) + ABAC (atributos: plan, region)
- **Rate limiting**: Redis token bucket por tenant/user/grupo (read/write)
- **Observabilidade**: logs JSON com correlation ID, métricas Prometheus, dashboards Grafana
- **Auditoria**: registro de logins, mudanças administrativas e denies

---

## 🚀 Quickstart (5 minutos)

### Opção 1: Docker Compose (recomendado)

```bash
cp .env.example .env
./scripts/up.sh
./scripts/migrate.sh
./scripts/seed.sh
./scripts/smoke.sh
```

URLs locais:
- **API Swagger**: http://localhost:8000/docs
- **RabbitMQ UI**: http://localhost:15672 (guest/guest)
- **Prometheus**: http://localhost:9090
- **Grafana**: http://localhost:3000 (admin/admin)

### Opção 2: Python local (3.12+)

```bash
python -m venv .venv
source .venv/bin/activate  # ou .\.venv\Scripts\Activate.ps1 no Windows
pip install -e . && pip install -r requirements-dev.txt
uvicorn src.api.main:app --reload --port 8000
```

---

## 🔐 Credenciais de exemplo

Seed cria:
- **Admin**: `admin@local` / `admin123` (tenant global, tid="*")
- **Ops**: `ops@demo` / `ops123` (tenant_demo)
- **Sales**: `sales@demo` / `sales123` (tenant_demo)

---

## 📚 API & Documentação

| Recurso | URL |
|---------|-----|
| Swagger UI | `/docs` |
| OpenAPI JSON | `/openapi.json` |
| Health check | `/healthz`, `/readyz` |
| Métricas | `/metrics` |
| Spec files | `docs/api/openapi.{json,yaml}` |

Exportar spec: `./scripts/api-export.sh`

---

## 📂 Estrutura do repositório

```
src/
├── api/                   # FastAPI routers + middlewares
├── worker/                # Dispatcher + handlers (RabbitMQ)
├── application/           # Lógica de negócio
├── infrastructure/        # DB, Redis, RabbitMQ
├── domain/                # Value objects e tipos
└── shared/                # Config, logs, metrics

migrations/               # Alembic (SQLAlchemy)
tests/                    # Unit + integration
docs/                     # OpenAPI, diagramas (mmd), screenshots
observability/            # Prometheus + Grafana
scripts/                  # up.sh, down.sh, migrate.sh, seed.sh, smoke.sh
```

---

## 🧪 Testes

```bash
python -m pytest tests/ -q
```

---

## ⚠️ Segurança em Produção

- Nunca use JWT_SECRET fraco — utilize Vault/Secrets Manager com >=32 bytes
- Configure TLS para todos os serviços
- Escaneie dependências por CVEs: `pip-audit`
- Ative HTTPS, CORS restritivo, CSRF tokens
- Migre JWT HS256 para RS256 (assimétrico) ou OIDC/Keycloak

---

## 📝 Contribuindo

1. Branch temática: `git checkout -b feat/descricao`
2. Testes passando: `pytest`
3. Lint/format: `ruff check .` → `black .` → `mypy`
4. PR com descrição clara

---

## 📄 Licença

MIT

