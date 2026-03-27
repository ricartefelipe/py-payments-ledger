from __future__ import annotations

import functools
import json as _json
import time
from dataclasses import dataclass
from typing import Any

import jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.infrastructure.db.models import AuditLog, Policy, RolePermission, Tenant, User
from src.shared.config import Settings
from src.shared.correlation import get_correlation_id
from src.shared.logging import get_logger
from src.shared.problem import http_problem

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
log = get_logger(__name__)


@functools.lru_cache(maxsize=1)
def _get_jwks_client(jwks_uri: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(jwks_uri, cache_keys=True)


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


def _audit(
    session: Session,
    tenant_id: str | None,
    actor: str,
    action: str,
    target: str,
    detail: dict[str, Any],
) -> None:
    try:
        session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_sub=actor,
                action=action,
                target=target,
                detail=detail,
                correlation_id=get_correlation_id(),
            )
        )
        session.commit()
    except Exception:
        session.rollback()
        log.warning("audit write failed", exc_info=True)


def authenticate_and_issue_token(
    session: Session, settings: Settings, email: str, password: str, tenant_id: str | None
) -> TokenResult:
    user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user or not pwd_ctx.verify(password, user.password_hash):
        _audit(
            session,
            tenant_id,
            email,
            "auth.login.failed",
            "/v1/auth/token",
            {"reason": "invalid_credentials"},
        )
        raise http_problem(401, "Unauthorized", "Invalid credentials", instance="/v1/auth/token")

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
        tid = str(user.tenant_id)
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

    _audit(
        session,
        tid if tid != "*" else tenant_id,
        email,
        "auth.login.success",
        "/v1/auth/token",
        {"roles": roles},
    )

    return TokenResult(access_token=token, expires_in=settings.token_expires_seconds)


def decode_token(settings: Settings, token: str) -> dict[str, Any]:
    try:
        if settings.jwks_uri:
            jwks_client = _get_jwks_client(settings.jwks_uri)
            signing_key = jwks_client.get_signing_key_from_jwt(token)
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=settings.jwt_issuer,
            )
        if settings.jwt_public_key and settings.jwt_algorithm.upper() == "RS256":
            return jwt.decode(
                token,
                settings.jwt_public_key,
                algorithms=["RS256"],
                issuer=settings.jwt_issuer,
            )
        # HS256: try current secret, then previous (rotation)
        try:
            return jwt.decode(
                token,
                settings.jwt_secret,
                algorithms=["HS256"],
                issuer=settings.jwt_issuer,
            )
        except Exception:
            if settings.jwt_secret_previous:
                try:
                    claims = jwt.decode(
                        token,
                        settings.jwt_secret_previous,
                        algorithms=["HS256"],
                        issuer=settings.jwt_issuer,
                    )
                    log.warning(
                        "Token verified with JWT_SECRET_PREVIOUS; consider completing rotation"
                    )
                    return claims
                except Exception:
                    pass
            raise
    except jwt.ExpiredSignatureError:
        raise http_problem(401, "Unauthorized", "Token expired", instance="auth")
    except Exception:
        raise http_problem(401, "Unauthorized", "Invalid token", instance="auth")


def try_decode_sub(settings: Settings, token: str) -> str | None:
    """Decode token silently for rate limit key; returns sub or None on any failure."""
    try:
        if settings.jwks_uri:
            jwks_client = _get_jwks_client(settings.jwks_uri)
            signing_key = jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=settings.jwt_issuer,
            )
        elif settings.jwt_public_key and settings.jwt_algorithm.upper() == "RS256":
            claims = jwt.decode(
                token,
                settings.jwt_public_key,
                algorithms=["RS256"],
                issuer=settings.jwt_issuer,
            )
        else:
            try:
                claims = jwt.decode(
                    token,
                    settings.jwt_secret,
                    algorithms=["HS256"],
                    issuer=settings.jwt_issuer,
                )
            except Exception:
                if settings.jwt_secret_previous:
                    claims = jwt.decode(
                        token,
                        settings.jwt_secret_previous,
                        algorithms=["HS256"],
                        issuer=settings.jwt_issuer,
                    )
                else:
                    return None
        return str(claims.get("sub") or "") or None
    except Exception:
        return None


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
    if principal.tid == "*" and "admin" in principal.roles:
        return

    if permission not in principal.perms:
        _audit(
            session,
            principal.tid,
            principal.sub,
            "authz.denied",
            permission,
            {"reason": "missing_permission", "permission": permission},
        )
        raise http_problem(403, "Forbidden", f"Missing permission: {permission}", instance="authz")

    policy = session.execute(
        select(Policy).where(Policy.permission_code == permission, Policy.enabled.is_(True))
    ).scalar_one_or_none()
    if not policy:
        _audit(
            session,
            principal.tid,
            principal.sub,
            "authz.denied",
            permission,
            {"reason": "no_policy", "permission": permission},
        )
        raise http_problem(403, "Forbidden", "No policy for permission", instance="abac")
    if policy.effect.upper() != "ALLOW":
        _audit(
            session,
            principal.tid,
            principal.sub,
            "authz.denied",
            permission,
            {"reason": "policy_deny", "permission": permission},
        )
        raise http_problem(403, "Forbidden", "Policy denies", instance="abac")
    plans_raw = policy.allowed_plans
    plans = (
        plans_raw if isinstance(plans_raw, list) else (_json.loads(plans_raw) if plans_raw else [])
    )
    if plans and principal.plan not in plans:
        _audit(
            session,
            principal.tid,
            principal.sub,
            "authz.denied",
            permission,
            {"reason": "plan_not_allowed", "plan": principal.plan, "permission": permission},
        )
        raise http_problem(
            403, "Forbidden", f"Plan '{principal.plan}' not allowed", instance="abac"
        )
    regions_raw = policy.allowed_regions
    regions = (
        regions_raw
        if isinstance(regions_raw, list)
        else (_json.loads(regions_raw) if regions_raw else [])
    )
    if regions and principal.region not in regions:
        _audit(
            session,
            principal.tid,
            principal.sub,
            "authz.denied",
            permission,
            {"reason": "region_not_allowed", "region": principal.region, "permission": permission},
        )
        raise http_problem(
            403, "Forbidden", f"Region '{principal.region}' not allowed", instance="abac"
        )
