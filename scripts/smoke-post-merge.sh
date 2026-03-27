#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PY_BIN="python3"
if [[ -x ".venv/bin/python" ]]; then
  PY_BIN=".venv/bin/python"
fi

echo "[smoke] py-payments-ledger post-merge smoke"
echo "[smoke] running quality + tests"
"${PY_BIN}" -m ruff check .
"${PY_BIN}" -m black --check .
"${PY_BIN}" -m mypy src
"${PY_BIN}" -m pytest -q
