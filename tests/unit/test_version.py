"""A versão da aplicação, a do empacotamento e a da release não podem divergir.

`pyproject.toml` dizia `1.0.0` e `create_app` repetia o literal `"1.0.0"`,
enquanto as tags do repositório já iam a `v1.3.1`. A divergência aparecia no
`info.version` do contrato OpenAPI publicado — ou seja, chegava ao consumidor.
Depois disso, as releases 2.0.3 a 2.0.6 abriram a entrada no CHANGELOG sem mudar
a constante, e a API seguiu anunciando 2.0.2: o último teste prende a versão à
entrada mais recente do CHANGELOG, então a release falha na CI se esquecer dela.
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


CHANGELOG = PYPROJECT.parent / "CHANGELOG.md"


def _latest_changelog_version() -> str:
    """Primeira entrada `## [X.Y.Z]` do CHANGELOG; ignora `## [Unreleased]`."""
    text = CHANGELOG.read_text(encoding="utf-8")
    match = re.search(r"^## \[(\d+\.\d+\.\d+)\]", text, re.MULTILINE)
    assert match, "nenhuma versão no CHANGELOG.md"
    return match.group(1)


def test_version_matches_latest_changelog_entry() -> None:
    latest = _latest_changelog_version()
    assert __version__ == latest, (
        f"CHANGELOG.md já registra {latest}, mas a aplicação anuncia {__version__}; "
        "atualize src/shared/version.py, pyproject.toml e docs/api/ na mesma mudança"
    )
