from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from src.application.security import Principal, authorize, build_principal, decode_token
from src.api.deps.db import get_db
from src.shared.problem import http_problem
from src.shared.correlation import set_subject, set_tenant_id


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


def enforce_tenant(
    principal: Principal,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
) -> str:
    if not x_tenant_id:
        raise http_problem(400, "Bad Request", "Missing X-Tenant-Id", instance="tenant")
    # admin global can access any tenant
    if principal.tid != "*" and principal.tid != x_tenant_id:
        raise http_problem(403, "Forbidden", "Tenant mismatch", instance="tenant")
    set_tenant_id(x_tenant_id)
    return x_tenant_id


def require_permission(permission: str):
    def _dep(
        principal: Principal = Depends(get_principal),
        tenant_id: str = Depends(enforce_tenant),
        db: Session = Depends(get_db),
    ) -> Principal:
        authorize(db, principal, permission)
        return principal

    return _dep
