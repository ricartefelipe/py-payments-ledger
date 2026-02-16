# Architecture Decisions

## ADR-001: Outbox pattern
We persist domain-relevant events into `outbox_events` in the same DB transaction as the state change.
A worker dispatches outbox rows to RabbitMQ, guaranteeing at-least-once delivery.

## ADR-002: Multi-tenant isolation (logical)
All business tables include `tenant_id` and every query is tenant-scoped.

## ADR-003: AuthN/AuthZ
JWT (HS256) for demo. RBAC provides coarse permissions; ABAC policies constrain by `plan` and `region`.
