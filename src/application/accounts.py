from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.infrastructure.db.models import AccountConfig
from src.shared.logging import get_logger
from src.shared.problem import http_problem

log = get_logger(__name__)


class AccountConfigDTO(BaseModel):
    id: str
    code: str
    label: str
    account_type: str
    is_default: bool


def list_accounts(session: Session, tenant_id: str) -> list[AccountConfigDTO]:
    rows = session.execute(
        select(AccountConfig)
        .where(AccountConfig.tenant_id == tenant_id)
        .order_by(AccountConfig.code)
    ).scalars().all()
    return [
        AccountConfigDTO(
            id=str(a.id), code=a.code, label=a.label,
            account_type=a.account_type, is_default=a.is_default,
        )
        for a in rows
    ]


def create_account(
    session: Session,
    tenant_id: str,
    code: str,
    label: str,
    account_type: str,
) -> AccountConfigDTO:
    if account_type not in ("ASSET", "LIABILITY", "EQUITY", "REVENUE", "EXPENSE"):
        raise http_problem(
            400, "Bad Request", f"invalid account_type: {account_type}",
            instance="/v1/accounts",
        )
    with session.begin():
        existing = session.execute(
            select(AccountConfig).where(
                AccountConfig.tenant_id == tenant_id,
                AccountConfig.code == code,
            )
        ).scalar_one_or_none()
        if existing:
            raise http_problem(
                409, "Conflict", f"account code {code} already exists for tenant",
                instance="/v1/accounts",
            )
        acc = AccountConfig(
            tenant_id=tenant_id,
            code=code,
            label=label,
            account_type=account_type,
            is_default=False,
        )
        session.add(acc)
        session.flush()

    return AccountConfigDTO(
        id=str(acc.id), code=acc.code, label=acc.label,
        account_type=acc.account_type, is_default=acc.is_default,
    )


def seed_default_accounts(session: Session, tenant_id: str) -> None:
    defaults = [
        ("CASH", "Cash", "ASSET", True),
        ("REVENUE", "Revenue", "REVENUE", True),
        ("REFUND_EXPENSE", "Refund Expense", "EXPENSE", True),
    ]
    for code, label, acc_type, is_default in defaults:
        existing = session.execute(
            select(AccountConfig).where(
                AccountConfig.tenant_id == tenant_id,
                AccountConfig.code == code,
            )
        ).scalar_one_or_none()
        if not existing:
            session.add(
                AccountConfig(
                    tenant_id=tenant_id,
                    code=code,
                    label=label,
                    account_type=acc_type,
                    is_default=is_default,
                )
            )
