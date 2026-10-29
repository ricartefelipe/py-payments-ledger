"""Versao do servico, em um unico lugar.

Existia em dois: `pyproject.toml` dizia `1.0.0` e `create_app` repetia o literal
`"1.0.0"`, enquanto as tags do repositorio já iam a `v1.3.1`. Duas fontes para o
mesmo dado divergem por omissao, e aqui a divergencia aparecia no `info.version`
do contrato OpenAPI publicado.

Esta constante e a fonte da aplicacao; `tests/unit/test_version.py` falha se ela
e o `pyproject.toml` discordarem.
"""

from __future__ import annotations

__version__ = "2.0.1"
