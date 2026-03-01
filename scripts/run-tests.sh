#!/usr/bin/env bash
# Roda testes unitários. Use venv se existir; caso contrário requer pytest instalado (pip install -r requirements-dev.txt).
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -d ".venv" ] && [ -x ".venv/bin/python" ]; then
  .venv/bin/python -m pytest tests/unit "$@"
else
  python3 -m pytest tests/unit "$@" 2>/dev/null || {
    echo "Pytest não encontrado. Crie o venv e instale as deps de teste:"
    echo "  python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt -r requirements-dev.txt"
    echo "  Depois: .venv/bin/python -m pytest tests/unit"
    exit 1
  }
fi
