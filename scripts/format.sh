#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
echo "Running black..."
python3 -m black .
echo "Running ruff --fix..."
python3 -m ruff check . --fix
