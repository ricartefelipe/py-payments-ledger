#!/bin/sh
set -e

echo "[entrypoint] Running migrations..."
alembic upgrade head

if [ "$APP_ENV" = "staging" ]; then
  echo "[entrypoint] Staging: running seed..."
  python -m src.infrastructure.db.seed || true
fi

echo "[entrypoint] Starting application..."
exec uvicorn src.api.main:app --host 0.0.0.0 --port 8000
