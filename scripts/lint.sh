#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
echo "Running ruff..."
python3 -m ruff check .
echo "Running black check..."
python3 -m black --check .
echo "Running mypy..."
python3 -m mypy src 2>/dev/null || true
