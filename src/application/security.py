from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.infrastructure.db.models import Policy, RolePermission, Tenant, User, UserRole
from src.shared.config import Settings
from src.shared.problem import http_problem
from src.shared.correlation import get_correlation_id
from src.shared.logging import get_logger

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
log = get_logger(__name__)


@dataclass(frozen=True)
class TokenResult:
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 3600


@dataclass(frozen=True)
class Principal:
    sub: str
    tid: str
    roles: list[str]
    perms: list[str]
    plan: str
    region: str
    jti: str
    ctx: dict[str, Any]


def authenticate_and_issue_token(
    session: Session, settings: Settings, email: str, password: str, tenant_id: str | None
) -> TokenResult:
    user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user or not pwd_ctx.verify(password, user.password_hash):
        raise http_problem(401, "Unauthorized", "Invalid credentials", instance="/v1/auth/token")

    # Resolve tenant context
    tid: str
    plan: str = "free"
    region: str = "region-a"

    if user.is_global_admin:
        tid = "*"
        if tenant_id:
            t = session.get(Tenant, tenant_id)
            if t:
                plan, region = t.plan, t.region
    else:
        if not user.tenant_id:
            raise http_problem(
                403, "Forbidden", "User has no tenant assigned", instance="/v1/auth/token"
            )
        tid = user.tenant_id
        t = session.get(Tenant, tid)
        if t:
            plan, region = t.plan, t.region

    roles = [ur.role_name for ur in user.roles]
    perms = _resolve_permissions(session, roles)

    now = int(time.time())
    exp = now + settings.token_expires_seconds
    jti = f"{user.id.hex}.{now}"

    claims: dict[str, Any] = {
        "iss": settings.jwt_issuer,
        "sub": email,
        "tid": tid,
        "roles": roles,
        "perms": perms,
        "plan": plan,
        "region": region,
        "iat": now,
        "exp": exp,
        "jti": jti,
        "ctx": {"email": email},
    }

    token = jwt.encode(claims, settings.jwt_secret, algorithm="HS256")
    return TokenResult(access_token=token, expires_in=settings.token_expires_seconds)


def decode_token(settings: Settings, token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token, settings.jwt_secret, algorithms=["HS256"], issuer=settings.jwt_issuer
        )
    except jwt.ExpiredSignatureError:
        raise http_problem(401, "Unauthorized", "Token expired", instance="auth")
    except Exception:
        raise http_problem(401, "Unauthorized", "Invalid token", instance="auth")


def build_principal(claims: dict[str, Any]) -> Principal:
    return Principal(
        sub=str(claims.get("sub", "")),
        tid=str(claims.get("tid", "")),
        roles=list(claims.get("roles") or []),
        perms=list(claims.get("perms") or []),
        plan=str(claims.get("plan") or "free"),
        region=str(claims.get("region") or "region-a"),
        jti=str(claims.get("jti") or ""),
        ctx=dict(claims.get("ctx") or {}),
    )


def _resolve_permissions(session: Session, roles: list[str]) -> list[str]:
    if not roles:
        return []
    rows = session.execute(
        select(RolePermission.permission_code).where(RolePermission.role_name.in_(roles))
    ).all()
    perms = sorted({r[0] for r in rows})
    return perms


def authorize(session: Session, principal: Principal, permission: str) -> None:
    # RBAC: explicit perms or admin role.
    if principal.tid == "*" and "admin" in principal.roles:
        return
    if permission not in principal.perms:
        raise http_problem(403, "Forbidden", f"Missing permission: {permission}", instance="authz")

    # ABAC policy
    policy = session.execute(
        select(Policy).where(Policy.permission_code == permission)
    ).scalar_one_or_none()
    if not policy:
        raise http_problem(403, "Forbidden", "No policy for permission", instance="abac")
    if policy.effect != "allow":
        raise http_problem(403, "Forbidden", "Policy denies", instance="abac")
    if policy.allowed_plans and principal.plan not in policy.allowed_plans:
        raise http_problem(
            403, "Forbidden", f"Plan '{principal.plan}' not allowed", instance="abac"
        )
    if policy.allowed_regions and principal.region not in policy.allowed_regions:
        raise http_problem(
            403, "Forbidden", f"Region '{principal.region}' not allowed", instance="abac"
        )
