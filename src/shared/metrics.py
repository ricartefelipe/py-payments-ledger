from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
)

PAYMENT_INTENTS_CREATED_TOTAL = Counter(
    "payment_intents_created_total",
    "Payment intents created",
    ["tenant_id"],
)

PAYMENT_INTENTS_CONFIRMED_TOTAL = Counter(
    "payment_intents_confirmed_total",
    "Payment intents confirmed",
    ["tenant_id"],
)

OUTBOX_PUBLISHED_TOTAL = Counter(
    "outbox_published_total",
    "Outbox events published to RabbitMQ",
    ["event_type"],
)

OUTBOX_FAILED_TOTAL = Counter(
    "outbox_failed_total",
    "Outbox publish failures",
    ["event_type"],
)

OUTBOX_PENDING_GAUGE = Gauge(
    "outbox_events_pending",
    "Number of outbox events pending dispatch",
)
