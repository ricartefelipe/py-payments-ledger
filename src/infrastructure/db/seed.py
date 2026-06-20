from __future__ import annotations

import os as _os
from datetime import datetime, timezone

from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.application.accounts import seed_default_accounts
from src.infrastructure.db.models import (
    AuditLog,
    FeatureFlag,
    Permission,
    Policy,
    Role,
    RolePermission,
    Tenant,
    User,
    UserRole,
)
from src.infrastructure.db.session import safe_begin
from src.shared.correlation import set_correlation_id, new_correlation_id
from src.shared.logging import get_logger

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_DEMO_TID = "00000000-0000-0000-0000-000000000002"


def _upsert_tenant(session: Session) -> None:
    existing = session.get(Tenant, _DEMO_TID)
    if existing:
        return
    session.add(Tenant(id=_DEMO_TID, name="Demo Tenant", plan="pro", region="us-east-1"))
    session.flush()


def _upsert_roles_permissions(session: Session) -> None:
    roles = ["admin", "ops", "sales"]
    perms = [
        "payments:write",
        "payments:read",
        "ledger:read",
        "admin:write",
        "profile:read",
        "analytics:read",
    ]
    for r in roles:
        if session.get(Role, r) is None:
            session.add(Role(name=r))
    for p in perms:
        if session.get(Permission, p) is None:
            session.add(Permission(code=p))
    session.flush()

    role_map = {
        "admin": perms,
        "ops": ["payments:write", "payments:read", "ledger:read", "profile:read", "analytics:read"],
        "sales": ["payments:read", "profile:read"],
    }
    for role, p_list in role_map.items():
        for p in p_list:
            exists = session.execute(
                select(RolePermission).where(
                    RolePermission.role_name == role, RolePermission.permission_code == p
                )
            ).scalar_one_or_none()
            if not exists:
                session.add(RolePermission(role_name=role, permission_code=p))
    session.flush()


def _upsert_policies(session: Session) -> None:
    policies: list[tuple[str, str, list[str], list[str]]] = [
        ("payments:write", "allow", ["pro", "enterprise"], []),
        ("payments:read", "allow", ["free", "pro", "enterprise"], []),
        ("ledger:read", "allow", ["pro", "enterprise"], []),
        ("admin:write", "allow", ["enterprise"], []),
        ("profile:read", "allow", ["free", "pro", "enterprise"], []),
        ("analytics:read", "allow", ["pro", "enterprise"], []),
    ]
    for perm, effect, plans, regions in policies:
        existing = session.execute(
            select(Policy).where(Policy.permission_code == perm).limit(1)
        ).scalar_one_or_none()
        if existing:
            existing.effect = effect
            existing.allowed_plans = plans
            existing.allowed_regions = regions
        else:
            session.add(
                Policy(
                    permission_code=perm,
                    effect=effect,
                    allowed_plans=plans,
                    allowed_regions=regions,
                )
            )
    session.flush()


def _upsert_users(session: Session) -> None:
    def upsert(
        email: str, password: str, tenant_id: str | None, is_global_admin: bool, role: str
    ) -> None:
        existing = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if not existing:
            u = User(
                email=email,
                password_hash=pwd_ctx.hash(password),
                tenant_id=tenant_id,
                is_global_admin=is_global_admin,
            )
            session.add(u)
            session.flush()
            session.add(UserRole(user_id=u.id, role_name=role))
        else:
            if existing.is_global_admin != is_global_admin:
                existing.is_global_admin = is_global_admin
            has_role = session.execute(
                select(UserRole).where(UserRole.user_id == existing.id, UserRole.role_name == role)
            ).scalar_one_or_none()
            if not has_role:
                session.add(UserRole(user_id=existing.id, role_name=role))

    upsert("admin@local", _os.environ.get("SEED_ADMIN_PASSWORD", "admin123"), None, True, "admin")
    upsert(
        "ops@demo.example.com",
        _os.environ.get("SEED_OPS_PASSWORD", "ops123"),
        str(_DEMO_TID),
        False,
        "ops",
    )
    upsert(
        "sales@demo.example.com",
        _os.environ.get("SEED_SALES_PASSWORD", "sales123"),
        str(_DEMO_TID),
        False,
        "sales",
    )
    session.flush()


def _upsert_flags(session: Session) -> None:
    flags: list[tuple[str, str, bool, int, list[str]]] = [
        (str(_DEMO_TID), "fast_settlement", True, 100, ["ops", "admin"]),
        (str(_DEMO_TID), "chaos_controls", True, 100, ["admin"]),
    ]
    for tenant_id, name, enabled, rollout, roles in flags:
        existing = session.execute(
            select(FeatureFlag).where(FeatureFlag.tenant_id == tenant_id, FeatureFlag.name == name)
        ).scalar_one_or_none()
        if existing:
            existing.enabled = enabled
            existing.rollout_percent = rollout
            existing.allowed_roles = roles
        else:
            session.add(
                FeatureFlag(
                    tenant_id=tenant_id,
                    name=name,
                    enabled=enabled,
                    rollout_percent=rollout,
                    allowed_roles=roles,
                )
            )
    session.flush()


def seed(session: Session) -> None:
    cid = new_correlation_id()
    set_correlation_id(cid)
    with safe_begin(session):
        _upsert_tenant(session)
        _upsert_roles_permissions(session)
        _upsert_policies(session)
        _upsert_users(session)
        _upsert_flags(session)
        seed_default_accounts(session, _DEMO_TID)
        session.add(
            AuditLog(
                tenant_id=None,
                actor_sub="system",
                action="seed",
                target="database",
                detail={"status": "ok"},
                correlation_id=cid,
            )
        )
    log.info("seed completed", extra={"correlation_id": cid})


def main() -> None:
    from src.shared.config import load_settings
    from src.infrastructure.db.session import init_db, session_scope

    settings = load_settings()
    init_db(settings)
    with session_scope() as session:
        seed(session)


if __name__ == "__main__":
    main()
