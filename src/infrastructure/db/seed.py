from __future__ import annotations

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
from src.shared.correlation import set_correlation_id
from src.shared.logging import get_logger
from src.shared.correlation import new_correlation_id

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _upsert_tenant(session: Session) -> None:
    tenant_id = "tenant_demo"
    existing = session.get(Tenant, tenant_id)
    if existing:
        return
    session.add(Tenant(id=tenant_id, name="Demo Tenant", plan="pro", region="region-a"))
    session.flush()


def _upsert_roles_permissions(session: Session) -> None:
    roles = ["admin", "ops", "sales"]
    perms = [
        "payments:write",
        "payments:read",
        "ledger:read",
        "admin:write",
        "profile:read",
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
        "ops": ["payments:write", "payments:read", "ledger:read", "profile:read"],
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
    policies = [
        ("payments:write", "allow", ["pro", "enterprise"], ["region-a", "region-b"]),
        ("payments:read", "allow", ["free", "pro", "enterprise"], ["region-a", "region-b"]),
        ("ledger:read", "allow", ["pro", "enterprise"], ["region-a", "region-b"]),
        ("admin:write", "allow", ["enterprise"], ["region-a", "region-b"]),
        ("profile:read", "allow", ["free", "pro", "enterprise"], ["region-a", "region-b"]),
    ]
    for perm, effect, plans, regions in policies:
        existing = session.execute(
            select(Policy).where(Policy.permission_code == perm)
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

    upsert("admin@local", "admin123", None, True, "admin")
    upsert("ops@demo", "ops123", "tenant_demo", False, "ops")
    upsert("sales@demo", "sales123", "tenant_demo", False, "sales")
    session.flush()


def _upsert_flags(session: Session) -> None:
    flags = [
        ("tenant_demo", "fast_settlement", True, 100, ["ops", "admin"]),
        ("tenant_demo", "chaos_controls", True, 100, ["admin"]),
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
    with session.begin():
        _upsert_tenant(session)
        _upsert_roles_permissions(session)
        _upsert_policies(session)
        _upsert_users(session)
        _upsert_flags(session)
        seed_default_accounts(session, "tenant_demo")
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
