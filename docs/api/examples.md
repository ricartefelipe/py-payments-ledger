# API Examples

Assumptions:
- API on `http://localhost:8000`
- Tenant: `tenant_demo`

## Get token
```bash
curl -sS -X POST http://localhost:8000/v1/auth/token \
  -H 'Content-Type: application/json' \
  -d '{"email":"ops@demo","password":"ops123","tenantId":"tenant_demo"}'
```

## Create payment intent
```bash
curl -sS -X POST http://localhost:8000/v1/payment-intents \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Id: tenant_demo" \
  -H "Content-Type: application/json" \
  -d '{"amount": 10.50, "currency":"BRL", "customer_ref":"CUST-1"}'
```

## Confirm (idempotent)
```bash
curl -sS -X POST http://localhost:8000/v1/payment-intents/$ID/confirm \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Id: tenant_demo" \
  -H "Idempotency-Key: demo-1"
```

## Ledger entries
```bash
curl -sS http://localhost:8000/v1/ledger/entries \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Id: tenant_demo"
```
