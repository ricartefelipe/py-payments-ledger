from __future__ import annotations

import base64
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.infrastructure.db.models import AuditLog

router = APIRouter(prefix="/v1", tags=["audit"])

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
EXPORT_LIMIT = 10_000


class AuditLogDTO(BaseModel):
    id: str
    tenant_id: str | None
    actor_sub: str
    action: str
    target: str
    detail: dict
    correlation_id: str
    created_at: str


class AuditLogPage(BaseModel):
    items: list[AuditLogDTO]
    next_cursor: str | None


def _encode_cursor(created_at: datetime, uid: str) -> str:
    raw = f"{created_at.isoformat()}|{uid}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    ts_str, uid = raw.rsplit("|", 1)
    ts = datetime.fromisoformat(ts_str)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts, uid


@router.get("/audit", response_model=AuditLogPage)
def list_audit_logs(
    action: str | None = Query(default=None),
    actor_sub: str | None = Query(default=None),
    start_date: datetime | None = Query(default=None),
    end_date: datetime | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("audit:read")),
):
    q = select(AuditLog).where(AuditLog.tenant_id == tenant_id)

    if action:
        q = q.where(AuditLog.action == action)
    if actor_sub:
        q = q.where(AuditLog.actor_sub == actor_sub)
    if start_date:
        q = q.where(AuditLog.created_at >= start_date)
    if end_date:
        q = q.where(AuditLog.created_at <= end_date)

    if cursor:
        cursor_ts, cursor_id = _decode_cursor(cursor)
        q = q.where(
            (AuditLog.created_at < cursor_ts)
            | ((AuditLog.created_at == cursor_ts) & (AuditLog.id < cursor_id))
        )

    q = q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit + 1)
    rows = db.execute(q).scalars().all()

    has_next = len(rows) > limit
    items = rows[:limit]

    next_cursor = None
    if has_next and items:
        last = items[-1]
        next_cursor = _encode_cursor(last.created_at, str(last.id))

    return AuditLogPage(
        items=[_to_dto(r) for r in items],
        next_cursor=next_cursor,
    )


@router.get("/audit/export")
def export_audit_logs(
    start_date: datetime | None = Query(default=None),
    end_date: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("audit:read")),
):
    q = select(AuditLog).where(AuditLog.tenant_id == tenant_id)

    if start_date:
        q = q.where(AuditLog.created_at >= start_date)
    if end_date:
        q = q.where(AuditLog.created_at <= end_date)

    q = q.order_by(AuditLog.created_at.desc()).limit(EXPORT_LIMIT)
    rows = db.execute(q).scalars().all()

    return JSONResponse(
        content=[_to_dto(r).model_dump() for r in rows],
        headers={"Content-Disposition": "attachment; filename=audit_export.json"},
    )


def _to_dto(row: AuditLog) -> AuditLogDTO:
    return AuditLogDTO(
        id=str(row.id),
        tenant_id=row.tenant_id,
        actor_sub=row.actor_sub,
        action=row.action,
        target=row.target,
        detail=row.detail,
        correlation_id=row.correlation_id,
        created_at=row.created_at.isoformat(),
    )
