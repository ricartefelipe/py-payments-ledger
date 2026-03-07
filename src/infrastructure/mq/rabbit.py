from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import pika
from pika.adapters.blocking_connection import BlockingChannel

from src.shared.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class RabbitConfig:
    url: str
    exchange: str = "payments.x"
    queue: str = "payments.events"
    dlq: str = "payments.dlq"


class Rabbit:
    MAX_RECONNECT_DELAY = 30

    def __init__(self, cfg: RabbitConfig) -> None:
        self._cfg = cfg
        self._conn: pika.BlockingConnection | None = None
        self._ch: BlockingChannel | None = None
        self._reconnect_delay = 1.0

    def connect(self) -> None:
        self.close()
        params = pika.URLParameters(self._cfg.url)
        params.heartbeat = 60
        params.blocked_connection_timeout = 120
        self._conn = pika.BlockingConnection(params)
        self._ch = self._conn.channel()
        self._ch.confirm_delivery()
        self._declare_topology()
        self._reconnect_delay = 1.0
        log.info("rabbit connected", extra={"url": self._cfg.url.split("@")[-1]})

    @property
    def is_open(self) -> bool:
        return (
            self._conn is not None
            and self._conn.is_open
            and self._ch is not None
            and self._ch.is_open
        )

    def _ensure_connected(self) -> None:
        if self.is_open:
            return
        while True:
            try:
                log.info("rabbit reconnecting", extra={"delay": self._reconnect_delay})
                self.connect()
                return
            except Exception:
                log.warning(
                    "rabbit reconnect failed, retrying",
                    extra={"delay": self._reconnect_delay},
                )
                time.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self.MAX_RECONNECT_DELAY
                )

    def close(self) -> None:
        try:
            if self._conn and self._conn.is_open:
                self._conn.close()
        except Exception:
            pass
        self._conn = None
        self._ch = None

    def _declare_topology(self) -> None:
        self._ch.exchange_declare(
            exchange=self._cfg.exchange, exchange_type="topic", durable=True
        )
        args = {
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": self._cfg.dlq,
        }
        self._ch.queue_declare(queue=self._cfg.queue, durable=True, arguments=args)
        self._ch.queue_declare(queue=self._cfg.dlq, durable=True)
        self._ch.queue_bind(
            queue=self._cfg.queue, exchange=self._cfg.exchange, routing_key="#"
        )

    def declare_external_queue(
        self, exchange: str, queue: str, routing_key: str = "#"
    ) -> None:
        self._ensure_connected()
        self._ch.exchange_declare(exchange=exchange, exchange_type="topic", durable=True)
        self._ch.queue_declare(queue=queue, durable=True)
        self._ch.queue_bind(queue=queue, exchange=exchange, routing_key=routing_key)

    def declare_external_queue_multi_bind(
        self, exchange: str, queue: str, routing_keys: list[str]
    ) -> None:
        self._ensure_connected()
        self._ch.exchange_declare(exchange=exchange, exchange_type="topic", durable=True)
        self._ch.queue_declare(queue=queue, durable=True)
        for rk in routing_keys:
            self._ch.queue_bind(queue=queue, exchange=exchange, routing_key=rk)

    def publish(
        self,
        routing_key: str,
        message: dict[str, Any],
        headers: Optional[dict[str, Any]] = None,
    ) -> None:
        self._ensure_connected()
        body = json.dumps(message, ensure_ascii=False).encode("utf-8")
        props = pika.BasicProperties(
            content_type="application/json",
            delivery_mode=2,
            headers=headers or {},
            timestamp=int(time.time()),
        )
        try:
            self._ch.basic_publish(
                exchange=self._cfg.exchange,
                routing_key=routing_key,
                body=body,
                properties=props,
                mandatory=False,
            )
        except (pika.exceptions.StreamLostError, pika.exceptions.ChannelWrongStateError,
                pika.exceptions.AMQPConnectionError, ConnectionResetError):
            log.warning("publish lost connection, reconnecting")
            self._ensure_connected()
            self._ch.basic_publish(
                exchange=self._cfg.exchange,
                routing_key=routing_key,
                body=body,
                properties=props,
                mandatory=False,
            )

    def consume(
        self,
        handler: Callable[[str, dict[str, Any], dict[str, Any]], None],
        prefetch: int = 10,
        queue: str | None = None,
    ) -> None:
        target_queue = queue or self._cfg.queue

        def _on_message(
            ch: BlockingChannel, method: Any, properties: pika.BasicProperties, body: bytes
        ) -> None:
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                log.exception("invalid json body")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return
            headers = properties.headers or {}
            rk = method.routing_key
            try:
                handler(rk, payload, headers)
                ch.basic_ack(delivery_tag=method.delivery_tag)
            except Exception:
                log.exception("handler error", extra={"routing_key": rk})
                ch.basic_reject(delivery_tag=method.delivery_tag, requeue=False)

        while True:
            try:
                self._ensure_connected()
                self._ch.basic_qos(prefetch_count=prefetch)
                self._ch.basic_consume(
                    queue=target_queue, on_message_callback=_on_message, auto_ack=False
                )
                log.info("consumer started", extra={"queue": target_queue})
                self._ch.start_consuming()
            except (pika.exceptions.StreamLostError, pika.exceptions.ChannelWrongStateError,
                    pika.exceptions.AMQPConnectionError, ConnectionResetError) as exc:
                log.warning("consumer connection lost, will reconnect", extra={"error": str(exc)})
                time.sleep(2)
            except Exception:
                log.exception("consumer fatal error")
                time.sleep(5)
