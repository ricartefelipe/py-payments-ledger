#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

python - <<'PY'
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

out = Path("docs/screenshots")
out.mkdir(parents=True, exist_ok=True)

def gen(name, text):
    img = Image.new("RGB", (1280, 720), (25, 25, 25))
    d = ImageDraw.Draw(img)
    d.rectangle([40,40,1240,680], outline=(200,200,200), width=3)
    d.text((80,80), name, fill=(255,255,255))
    d.text((80,140), text, fill=(220,220,220))
    img.save(out/name)

gen("swagger.png", "Placeholder screenshot. Replace with real Swagger UI capture.")
gen("grafana-overview.png", "Placeholder screenshot. Replace with Grafana dashboard capture.")
gen("trace-example.png", "Placeholder screenshot. Replace with trace viewer capture.")
gen("sequence-main-flow.png", "Placeholder screenshot. Mermaid sequence diagram (export).")
print("Generated placeholders in docs/screenshots/")
PY
