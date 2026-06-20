#!/usr/bin/env bash
# Cria ou repara .venv quando python3-venv não está instalado (Debian/Ubuntu).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PY="${VENV_DIR}/bin/python3"

venv_pip_ok() {
  [[ -x "${PY}" ]] && "${PY}" -m pip --version >/dev/null 2>&1
}

if venv_pip_ok; then
  exit 0
fi

echo "[bootstrap-venv] Preparando ${VENV_DIR}..."

if [[ -d "${VENV_DIR}" ]] && ! venv_pip_ok; then
  rm -rf "${VENV_DIR}"
fi

if ! (cd "${ROOT_DIR}" && python3 -m venv .venv 2>/dev/null); then
  echo "[bootstrap-venv] python3 -m venv indisponível (instale python3.12-venv se puder)."
  echo "[bootstrap-venv] Tentando bootstrap com get-pip..."
  mkdir -p "${VENV_DIR}/bin"
  if [[ ! -x "${PY}" ]]; then
    ln -sf "$(command -v python3)" "${PY}"
  fi
  tmp_pip="$(mktemp)"
  curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "${tmp_pip}"
  "${PY}" "${tmp_pip}"
  rm -f "${tmp_pip}"
fi

if ! venv_pip_ok; then
  echo "[ERRO] Não foi possível preparar pip em ${VENV_DIR}."
  echo "       Opção A: sudo apt install python3.12-venv && apague .venv && rode de novo."
  echo "       Opção B: python3 -m pip install --break-system-packages -e '.[dev]' na raiz do repo."
  exit 1
fi

echo "[bootstrap-venv] OK — use: ${PY} -m pip install -e '.[dev]'"
