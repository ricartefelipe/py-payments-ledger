#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

./scripts/up.sh
./scripts/migrate.sh
./scripts/seed.sh

API_BASE="http://localhost:8000"
TENANT="tenant_demo"

echo "Getting token..."
TOKEN_JSON=$(curl -sS -X POST "$API_BASE/v1/auth/token" \
  -H "Content-Type: application/json" \
  -d '{"email":"ops@demo","password":"ops123","tenantId":"tenant_demo"}')

TOKEN=$(python - <<PY
import json,sys
obj=json.loads(sys.argv[1])
print(obj["access_token"])
PY
"$TOKEN_JSON")

echo "Creating payment intent..."
PI_JSON=$(curl -sS -X POST "$API_BASE/v1/payment-intents" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Id: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{"amount": 10.50, "currency":"BRL", "customer_ref":"CUST-1"}')

PI_ID=$(python - <<PY
import json,sys
obj=json.loads(sys.argv[1])
print(obj["id"])
PY
"$PI_JSON")

echo "Confirming payment intent (idempotent)..."
curl -sS -X POST "$API_BASE/v1/payment-intents/$PI_ID/confirm" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Id: $TENANT" \
  -H "Idempotency-Key: smoke-1" > /dev/null

echo "Waiting worker to post ledger..."
sleep 2

echo "Fetching ledger entries..."
curl -sS "$API_BASE/v1/ledger/entries" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Id: $TENANT" > /dev/null

echo "Negative test (tenant mismatch should 403)..."
HTTP_CODE=$(curl -sS -o /dev/null -w "%{http_code}" "$API_BASE/v1/ledger/entries" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Id: other_tenant")
if [ "$HTTP_CODE" != "403" ]; then
  echo "Expected 403, got $HTTP_CODE"
  exit 1
fi

echo "Smoke OK ✅"
