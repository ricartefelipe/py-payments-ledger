"""A versão da aplicação e a do empacotamento não podem divergir.

`pyproject.toml` dizia `1.0.0` e `create_app` repetia o literal `"1.0.0"`,
enquanto as tags do repositório já iam a `v1.3.1`. A divergência aparecia no
`info.version` do contrato OpenAPI publicado — ou seja, chegava ao consumidor.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.shared.version import __version__

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _pyproject_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    # Primeira ocorrência sob [project]; evita casar os pins de dependência.
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "version não encontrada no pyproject.toml"
    return match.group(1)


def test_app_version_matches_pyproject() -> None:
    assert __version__ == _pyproject_version(), (
        "src/shared/version.py e pyproject.toml divergiram; " "atualize os dois na mesma mudança"
    )


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), __version__


def test_openapi_reports_the_same_version() -> None:
    """O contrato publicado precisa anunciar a versão real."""
    import json

    spec_path = PYPROJECT.parent / "docs" / "api" / "openapi.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    assert spec["info"]["version"] == __version__
