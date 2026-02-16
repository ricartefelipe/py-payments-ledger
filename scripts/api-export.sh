#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

python - <<'PY'
import json, yaml
from src.api.main import create_app
app = create_app()
spec = app.openapi()
open('docs/api/openapi.json','w',encoding='utf-8').write(json.dumps(spec, indent=2, ensure_ascii=False))
open('docs/api/openapi.yaml','w',encoding='utf-8').write(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True))
print("Exported to docs/api/openapi.json and docs/api/openapi.yaml")
PY
