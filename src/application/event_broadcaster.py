from __future__ import annotations

import asyncio
import json
import uuid
from collections import defaultdict

from src.shared.logging import get_logger

log = get_logger(__name__)


class EventBroadcaster:
    """In-process SSE broadcaster with multi-tenant isolation."""

    def __init__(self) -> None:
        self._clients: dict[str, list[asyncio.Queue[str | None]]] = defaultdict(list)

    async def subscribe(self, tenant_id: str) -> asyncio.Queue[str | None]:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._clients[tenant_id].append(queue)
        log.info(
            "SSE client subscribed",
            extra={"tenant_id": tenant_id, "total": len(self._clients[tenant_id])},
        )
        return queue

    def unsubscribe(self, tenant_id: str, queue: asyncio.Queue[str | None]) -> None:
        queues = self._clients.get(tenant_id)
        if queues and queue in queues:
            queues.remove(queue)
            if not queues:
                del self._clients[tenant_id]
        log.info(
            "SSE client unsubscribed",
            extra={"tenant_id": tenant_id, "total": len(self._clients.get(tenant_id, []))},
        )

    async def broadcast(self, tenant_id: str, event_type: str, data: dict) -> None:
        queues = self._clients.get(tenant_id)
        if not queues:
            return

        event_id = str(uuid.uuid4())
        payload = json.dumps(data, default=str)
        message = f"id: {event_id}\nevent: {event_type}\ndata: {payload}\n\n"

        disconnected: list[asyncio.Queue[str | None]] = []
        for q in queues:
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                disconnected.append(q)

        for q in disconnected:
            queues.remove(q)

        if queues:
            log.debug(
                "Broadcast %s to %d client(s) for tenant %s",
                event_type,
                len(queues),
                tenant_id,
            )

    def broadcast_sync(self, tenant_id: str, event_type: str, data: dict) -> None:
        """Fire-and-forget broadcast usable from synchronous code."""
        queues = self._clients.get(tenant_id)
        if not queues:
            return

        event_id = str(uuid.uuid4())
        payload = json.dumps(data, default=str)
        message = f"id: {event_id}\nevent: {event_type}\ndata: {payload}\n\n"

        disconnected: list[asyncio.Queue[str | None]] = []
        for q in queues:
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                disconnected.append(q)

        for q in disconnected:
            queues.remove(q)

    def get_client_count(self, tenant_id: str) -> int:
        return len(self._clients.get(tenant_id, []))


broadcaster = EventBroadcaster()
