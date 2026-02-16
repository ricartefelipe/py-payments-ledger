#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "Creating .env from .env.example"
  cp .env.example .env
fi

docker compose up -d --build

echo ""
echo "API:        http://localhost:8000/docs"
echo "Rabbit UI:  http://localhost:15672  (guest/guest)"
echo "Prometheus: http://localhost:9090"
echo "Grafana:    http://localhost:3000  (admin/admin)"
