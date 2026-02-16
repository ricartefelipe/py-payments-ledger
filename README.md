# py-payments-ledger

FastAPI + Worker demo: **payments** + **double-entry ledger** with **outbox**, **idempotency**, **RBAC/ABAC**, **Redis rate limit**, and **Prometheus/Grafana**.

## What you get
- **Outbox pattern**: API writes state + outbox in the same DB transaction; worker dispatches to RabbitMQ.
- **At-least-once events** (RabbitMQ) + **DLQ**
- **Idempotent confirm** via `Idempotency-Key` (Redis)
- **Multi-tenant** via `X-Tenant-Id`
- **AuthN/AuthZ**: JWT (HS256 demo) + RBAC + ABAC (plan + region)
- **Observability**: `/metrics` scraped by Prometheus; Grafana provisioned dashboards.

## Quickstart (10 minutes)
```bash
cp .env.example .env
./scripts/up.sh
./scripts/migrate.sh
./scripts/seed.sh
./scripts/smoke.sh
```

URLs:
- API docs: http://localhost:8000/docs
- RabbitMQ UI: http://localhost:15672 (guest/guest)
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (admin/admin)

## Demo credentials
Seed creates:
- Global admin: `admin@local` / `admin123` (tid="*")
- Ops user: `ops@demo` / `ops123` (tenant_demo)
- Sales user: `sales@demo` / `sales123` (tenant_demo)

## API docs
- Swagger UI: `/docs`
- OpenAPI: `/openapi.json`
- Versioned spec files in `docs/api/openapi.{json,yaml}`
- Export/refresh: `./scripts/api-export.sh`

## Why this matters
This repo demonstrates production-flavored concerns: consistent event publication (outbox), idempotency, tenant isolation, policy-driven authorization (ABAC), and operability (metrics + dashboards).

## Folder guide
- `src/api`: FastAPI routers + middleware
- `src/worker`: outbox dispatcher + consumer
- `migrations`: Alembic migrations
- `docs/architecture`: mermaid diagrams
- `observability`: Prometheus/Grafana provisioning
- `scripts`: reproducible commands

## License
MIT
