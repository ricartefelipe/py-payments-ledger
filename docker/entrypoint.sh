#!/bin/sh
set -e

is_staging() {
  [ "${APP_ENV:-}" = "staging" ] || [ "${RAILWAY_ENVIRONMENT:-}" = "staging" ]
}

echo "[entrypoint] Running migrations..."
set +e
out=$(alembic upgrade head 2>&1)
ec=$?
set -e
echo "$out"

if [ "$ec" -eq 0 ]; then
  :
elif echo "$out" | grep -qi "DuplicateTable"; then
  echo "[entrypoint] Existing schema sem baseline Alembic (DuplicateTable). Seguindo startup sem migration destrutiva."
elif is_staging && echo "$out" | grep -qiE 'relation.*permissions.*does not exist|UndefinedTable.*permissions'; then
  echo "[entrypoint] Staging: BD inconsistente (versão Alembic à frente do schema). Aplicando stamp base + upgrade completo..."
  alembic stamp base
  set +e
  out2=$(alembic upgrade head 2>&1)
  ec2=$?
  set -e
  echo "$out2"
  if [ "$ec2" -ne 0 ]; then
    if echo "$out2" | grep -qi "DuplicateTable"; then
      echo "[entrypoint] DuplicateTable após re-aplicar — schema já existia; seguindo."
    else
      echo "[entrypoint] Migration failed after recovery."
      exit 1
    fi
  fi
else
  echo "[entrypoint] Migration failed and no safe fallback matched."
  exit 1
fi

echo "[entrypoint] Alembic revision aplicada na BD:"
alembic current 2>&1 || true

if is_staging; then
  echo "[entrypoint] Staging: running seed..."
  if ! python -m src.infrastructure.db.seed; then
    echo "[entrypoint] Seed failed — aborting startup (staging must not run with broken policies/users)."
    exit 1
  fi
fi

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

echo "[entrypoint] Starting application..."
exec uvicorn src.api.main:app --host 0.0.0.0 --port 8000
