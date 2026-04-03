#!/bin/sh
set -e

echo "[entrypoint] Running migrations..."
if out=$(alembic upgrade head 2>&1); then
  echo "$out"
else
  echo "$out"
  if echo "$out" | grep -qi "DuplicateTable"; then
    echo "[entrypoint] Existing schema sem baseline Alembic (DuplicateTable). Seguindo startup sem migration destrutiva."
  else
    echo "[entrypoint] Migration failed and no safe fallback matched."
    exit 1
  fi
fi

if [ "$APP_ENV" = "staging" ]; then
  echo "[entrypoint] Staging: running seed..."
  if ! python -m src.infrastructure.db.seed; then
    echo "[entrypoint] Seed failed — aborting startup (staging must not run with broken policies/users)."
    exit 1
  fi
fi

if [ "$#" -gt 0 ]; then
  # Permite executar comandos (ex: seed/migrations) via `docker compose run ... <cmd>`.
  exec "$@"
fi

echo "[entrypoint] Starting application..."
exec uvicorn src.api.main:app --host 0.0.0.0 --port 8000
