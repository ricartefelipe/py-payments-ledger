from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from src.api.deps.auth import enforce_tenant
from src.application.event_broadcaster import broadcaster
from src.shared.logging import get_logger

log = get_logger(__name__)

router = APIRouter(tags=["events"])

HEARTBEAT_INTERVAL = 30


async def _event_generator(
    request: Request,
    tenant_id: str,
    queue: asyncio.Queue[str | None],
) -> AsyncGenerator[str, None]:
    try:
        yield f"id: {uuid.uuid4()}\nevent: connected\ndata: {json.dumps({'tenantId': tenant_id})}\n\n"

        while True:
            if await request.is_disconnected():
                break

            try:
                message = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL)
                if message is None:
                    break
                yield message
            except asyncio.TimeoutError:
                yield f"id: {uuid.uuid4()}\nevent: heartbeat\ndata: \n\n"
    finally:
        broadcaster.unsubscribe(tenant_id, queue)


@router.get("/v1/events/stream")
async def event_stream(
    request: Request,
    tenant_id: str = Depends(enforce_tenant),
):
    queue = await broadcaster.subscribe(tenant_id)
    return StreamingResponse(
        _event_generator(request, tenant_id, queue),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/v1/events/clients")
async def get_client_count(
    tenant_id: str = Depends(enforce_tenant),
):
    return {"tenantId": tenant_id, "activeClients": broadcaster.get_client_count(tenant_id)}
