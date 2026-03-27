#!/usr/bin/env bash
set -euo pipefail

# Smoke HTTP pos-deploy (staging). Health + OpenAPI.
#
# Variaveis:
#   PAYMENTS_SMOKE_URL ou SMOKE_BASE_URL
#   SMOKE_REQUIRE_URL=1 — exige URL

BASE_URL="${PAYMENTS_SMOKE_URL:-${SMOKE_BASE_URL:-}}"
REQUIRE="${SMOKE_REQUIRE_URL:-0}"

if [[ -z "$BASE_URL" ]]; then
  echo "[smoke] PAYMENTS_SMOKE_URL/SMOKE_BASE_URL nao definido — smoke HTTP ignorado."
  [[ "$REQUIRE" == "1" ]] && exit 1
  exit 0
fi

BASE_URL="${BASE_URL%/}"
echo "[smoke] py-payments-ledger — base ${BASE_URL}"

curl -sfS --max-time 20 "$BASE_URL/healthz" >/dev/null || {
  echo "[smoke] FALHA: GET /healthz"
  exit 1
}
echo "[smoke] OK /healthz"

BODY=$(curl -sfS --max-time 20 "$BASE_URL/openapi.json") || {
  echo "[smoke] FALHA: GET /openapi.json"
  exit 1
}
if ! grep -q openapi <<<"$BODY"; then
  echo "[smoke] FALHA: openapi.json invalido"
  exit 1
fi
echo "[smoke] OK /openapi.json"
echo "[smoke] concluido com sucesso"
