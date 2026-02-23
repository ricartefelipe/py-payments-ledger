# Demonstração em 3 minutos

## O que rodar

```bash
# 1. Subir tudo (infra + migrate)
./scripts/up.sh

# 2. Seed (usuários, tenants, permissões)
./scripts/seed.sh

# 3. Smoke (valida fluxo completo)
./scripts/smoke.sh
```

## O que mostrar

### 1. Health e docs

- `curl http://localhost:8000/healthz` → `{"status":"ok"}`
- `curl http://localhost:8000/readyz` → `{"status":"ok"}` (DB + Redis)
- Swagger: http://localhost:8000/docs

### 2. Fluxo de pagamento (API)

```bash
# Token
TOKEN=$(curl -sS -X POST http://localhost:8000/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"email":"ops@demo","password":"ops123","tenantId":"tenant_demo"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Criar (Idempotency-Key obrigatório)
curl -X POST http://localhost:8000/v1/payment-intents \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Id: tenant_demo" \
  -H "Idempotency-Key: demo-$(date +%s)" \
  -H "Content-Type: application/json" \
  -d '{"amount":99.90,"currency":"BRL","customer_ref":"DEMO-1"}'

# Confirmar
# (usar id retornado no create)
curl -X POST http://localhost:8000/v1/payment-intents/<ID>/confirm \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Id: tenant_demo" \
  -H "Idempotency-Key: demo-confirm-1"

# Ledger (após ~3s)
curl http://localhost:8000/v1/ledger/balances \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Id: tenant_demo"
```

### 3. Integração por eventos (RabbitMQ)

Com `ORDERS_INTEGRATION_ENABLED=true` no `.env`:

```bash
# Publicar charge_requested
docker compose run --rm api python3 scripts/publish_charge_request.py ord-123 tenant_demo 50.00

# Em ~5s: verificar ledger com nova entrada
curl http://localhost:8000/v1/ledger/entries \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Id: tenant_demo"
```

### 4. Observabilidade

- **Métricas:** http://localhost:8000/metrics  
  `payment_intents_created_total`, `outbox_events_pending`, etc.
- **Grafana:** http://localhost:3000 (admin/admin)
- **RabbitMQ:** http://localhost:15672 (guest/guest)

### 5. Comandos que passam

```bash
./scripts/lint.sh       # ruff + black check
./scripts/format.sh     # black + ruff --fix
python3 -m pytest tests/ -v
```
