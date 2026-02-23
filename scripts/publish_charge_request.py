#!/usr/bin/env python3
"""Publish payment.charge_requested to RabbitMQ (for smoke/integration tests)."""

from __future__ import annotations

import json
import os
import sys
import uuid

import pika

EXCHANGE = os.getenv("ORDERS_EXCHANGE", "orders.x")
ROUTING_KEY = "payment.charge_requested"
URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")


def main() -> int:
    order_id = sys.argv[1] if len(sys.argv) > 1 else str(uuid.uuid4())
    tenant_id = sys.argv[2] if len(sys.argv) > 2 else "tenant_demo"
    amount = float(sys.argv[3]) if len(sys.argv) > 3 else 25.00

    payload = {
        "order_id": order_id,
        "tenant_id": tenant_id,
        "total_amount": amount,
        "currency": "BRL",
        "customer_ref": f"CUST-{order_id[:8]}",
        "correlation_id": str(uuid.uuid4()),
    }

    params = pika.URLParameters(URL)
    conn = pika.BlockingConnection(params)
    ch = conn.channel()
    ch.exchange_declare(exchange=EXCHANGE, exchange_type="topic", durable=True)
    ch.basic_publish(
        exchange=EXCHANGE,
        routing_key=ROUTING_KEY,
        body=json.dumps(payload),
        properties=pika.BasicProperties(
            content_type="application/json",
            delivery_mode=2,
            headers={"X-Correlation-Id": payload["correlation_id"], "X-Tenant-Id": tenant_id},
        ),
    )
    conn.close()
    print(json.dumps({"order_id": order_id, "tenant_id": tenant_id}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
