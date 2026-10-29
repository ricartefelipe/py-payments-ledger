from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from src.api.deps.db import get_db
from src.application.security import Principal, authorize, build_principal, decode_token
from src.shared.correlation import set_subject, set_tenant_id
from src.shared.logging import get_logger
from src.shared.problem import http_problem

log = get_logger(__name__)


def _get_settings(request: Request):
    return request.app.state.settings


def get_principal(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> Principal:
    if not authorization or not authorization.startswith("Bearer "):
        raise http_problem(401, "Unauthorized", "Missing bearer token", instance="auth")
    token = authorization.removeprefix("Bearer ").strip()
    settings = _get_settings(request)
    claims = decode_token(settings, token)
    principal = build_principal(claims)
    set_subject(principal.sub)
    return principal


#: Valor de `tid` que concede acesso a qualquer tenant.
#:
#: E um privilegio amplo: um token com este `tid` opera sobre o dinheiro de
#: todos os tenants, e por muito tempo isso foi concedido apenas pela presenca
#: do claim, sem permissao correspondente e sem rastro.
TENANT_WILDCARD = "*"

#: Permissao que passa a autorizar o acesso cross-tenant.
#:
#: O claim `tid` e emitido por outro servico (`spring-saas-core`), portanto
#: exigir a permissao de imediato invalidaria tokens administrativos em uso.
#: A transicao e controlada por `CROSS_TENANT_REQUIRE_PERMISSION`:
#:
#: * `false` (default) — comportamento atual, com aviso em log a cada uso e um
#:   aviso adicional quando a permissao esta ausente;
#: * `true` — o claim sozinho nao basta; a permissao e obrigatoria.
#:
#: O que o emissor precisa passar a incluir esta em docs/contracts/identity.md.
CROSS_TENANT_PERMISSION = "tenant:any"


def enforce_tenant(
    request: Request,
    principal: Principal = Depends(get_principal),
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
) -> str:
    if not x_tenant_id:
        raise http_problem(400, "Bad Request", "Missing X-Tenant-Id", instance="tenant")

    if principal.tid == TENANT_WILDCARD:
        if principal.tid != x_tenant_id:
            _authorize_cross_tenant(request, principal, x_tenant_id)
    elif principal.tid != x_tenant_id:
        raise http_problem(403, "Forbidden", "Tenant mismatch", instance="tenant")

    set_tenant_id(x_tenant_id)
    return x_tenant_id


def _authorize_cross_tenant(request: Request, principal: Principal, target: str) -> None:
    """Decide e registra um acesso administrativo a outro tenant.

    O acesso cross-tenant deixa de ser silencioso: cada uso e registrado com
    ator, tenant alvo e papeis. Quando a permissao explicita estiver exigida, a
    ausencia dela passa a ser negativa — e, enquanto nao estiver, ja aparece
    como aviso, para que a lacuna seja visivel antes de virar bloqueio.
    """
    has_permission = CROSS_TENANT_PERMISSION in (principal.perms or [])
    settings = _get_settings(request)
    required = getattr(settings, "cross_tenant_require_permission", False)

    if required and not has_permission:
        log.warning(
            "cross-tenant access denied: explicit permission is required",
            extra={
                "actor": principal.sub,
                "target_tenant": target,
                "required_permission": CROSS_TENANT_PERMISSION,
            },
        )
        raise http_problem(
            403,
            "Forbidden",
            f"cross-tenant access requires the {CROSS_TENANT_PERMISSION} permission",
            instance="tenant",
        )

    log.warning(
        "cross-tenant access granted by wildcard tid",
        extra={
            "actor": principal.sub,
            "target_tenant": target,
            "roles": ",".join(principal.roles or []),
            "has_explicit_permission": has_permission,
        },
    )
    if not has_permission:
        log.warning(
            "cross-tenant token has no explicit permission; it will be refused "
            "once CROSS_TENANT_REQUIRE_PERMISSION is enabled",
            extra={
                "actor": principal.sub,
                "required_permission": CROSS_TENANT_PERMISSION,
            },
        )


def require_permission(permission: str):
    def _dep(
        principal: Principal = Depends(get_principal),
        tenant_id: str = Depends(enforce_tenant),
        db: Session = Depends(get_db),
    ) -> Principal:
        authorize(db, principal, permission)
        return principal

    return _dep
