#!/usr/bin/env bash
set -euo pipefail

# Smoke HTTP pos-deploy (staging). Health + OpenAPI; opcionalmente readiness
# (DB + Redis e, se o servidor tiver READINESS_REQUIRE_RABBIT=true, ligação ao RabbitMQ).
#
# Variaveis:
#   PAYMENTS_SMOKE_URL ou SMOKE_BASE_URL
#   SMOKE_REQUIRE_URL=1 — exige URL
#   SMOKE_CHECK_READYZ=1 — chama GET /readyz (recomendado em CI quando staging tem stack completa)

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

if [[ "${SMOKE_CHECK_READYZ:-0}" == "1" ]]; then
  curl -sfS --max-time 25 "$BASE_URL/readyz" >/dev/null || {
    echo "[smoke] FALHA: GET /readyz (DB/Redis/Rabbit selo servidor)"
    exit 1
  }
  echo "[smoke] OK /readyz"
fi

echo "[smoke] concluido com sucesso"
