#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p docs/api

docker compose run --rm --no-deps \
  -v "$(pwd)/docs/api:/app/docs/api" \
  api python -c "
import json, yaml
from src.api.main import create_app
app = create_app()
spec = app.openapi()
with open('docs/api/openapi.json','w',encoding='utf-8') as f:
    json.dump(spec, f, indent=2, ensure_ascii=False)
with open('docs/api/openapi.yaml','w',encoding='utf-8') as f:
    yaml.safe_dump(spec, f, sort_keys=False, allow_unicode=True)
print('Exported to docs/api/openapi.json and docs/api/openapi.yaml')
"

echo "Done. Files in docs/api/"
