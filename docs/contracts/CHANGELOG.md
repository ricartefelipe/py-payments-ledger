# Contract Versioning Changelog — py-payments-ledger

This document records breaking changes to the REST API and event contracts. It establishes the baseline (v1.0.0) and defines the format, policy, and deprecation process for future versions.

See [events.md](events.md), [identity.md](identity.md), and [headers.md](headers.md) for the full contract specifications.

---

## Versioning policy

### REST API

- **Versioning model:** URL path prefix (`/v1/`, `/v2/`, …).
- **Semantic versioning:** API versions follow `MAJOR.MINOR` (e.g. v1.0, v2.0).
  - **MAJOR:** Breaking changes (removed/renamed endpoints, incompatible request/response shapes).
  - **MINOR:** Non-breaking additions (new endpoints, new optional fields).
- **Current stable:** `v1` (v1.0.0 baseline).
- **Backward compatibility:** Within a major version (e.g. v1.x), breaking changes are not allowed. New minor versions may add optional fields or new endpoints.

### Events (RabbitMQ)

- **Schema versioning:** Event payloads may include a `schemaVersion` or `eventVersion` field. When omitted, `1` is assumed.
- **Compatibility:** Consumers must tolerate extra fields (forward compatibility). Producers must not remove or rename existing fields without a new schema version.
- **Breaking changes:** Removing fields, renaming fields, or changing types in existing events require a new schema version and must be documented here.

---

## Deprecation process

1. **Announce** — Add a deprecation notice in this changelog and in API responses (e.g. `Deprecation` header or `X-API-Deprecation`).
2. **Deprecate** — Mark the contract as deprecated. Minimum 6 months from announce.
3. **Remove** — Remove the deprecated contract in the next major version. Minimum 3 months from deprecate.

| Phase    | Minimum duration | Actions                                                                 |
|----------|------------------|-------------------------------------------------------------------------|
| Announce | —                | Document in changelog, add `Deprecation` header, notify integrators     |
| Deprecate| 6 months         | Mark deprecated, return warnings if configured                          |
| Remove   | 3 months after deprecate | Remove in next MAJOR; keep previous major available during overlap |

---

## Contract surface (v1.0.0 baseline)

### REST API — Endpoints

| Method | Path | Idempotency-Key | Description |
|--------|------|-----------------|-------------|
| POST | `/v1/auth/token` | — | Issue JWT (local/dev) |
| GET | `/v1/me` | — | Current user from JWT |
| GET | `/v1/audit` | — | List audit log |
| GET | `/v1/audit/export` | — | Export audit log (CSV/JSON) |
| GET | `/v1/payment-intents` | — | List payment intents |
| POST | `/v1/payment-intents` | Required | Create payment intent |
| GET | `/v1/payment-intents/:pid` | — | Get payment intent |
| POST | `/v1/payment-intents/:pid/confirm` | Required | Confirm payment intent |
| POST | `/v1/payment-intents/:pid/void` | Required | Void payment intent |
| POST | `/v1/payment-intents/:pid/refund` | — | Create refund |
| GET | `/v1/payment-intents/:pid/refunds` | — | List refunds |
| POST | `/v1/invoices` | — | Create invoice |
| GET | `/v1/invoices` | — | List invoices |
| GET | `/v1/invoices/:id` | — | Get invoice |
| POST | `/v1/invoices/:id/issue` | — | Issue invoice |
| POST | `/v1/invoices/:id/pay` | — | Mark invoice paid |
| POST | `/v1/invoices/:id/cancel` | — | Cancel invoice |
| POST | `/v1/recurring-charges` | — | Create recurring charge |
| GET | `/v1/recurring-charges` | — | List recurring charges |
| POST | `/v1/recurring-charges/:id/pause` | — | Pause recurring charge |
| POST | `/v1/recurring-charges/:id/resume` | — | Resume recurring charge |
| POST | `/v1/recurring-charges/:id/cancel` | — | Cancel recurring charge |
| GET | `/v1/ledger/entries` | — | List ledger entries |
| GET | `/v1/ledger/balances` | — | Get account balances |
| GET | `/v1/reports/revenue` | — | Revenue report |
| GET | `/v1/reports/account-balances` | — | Account balance report |
| POST | `/v1/reconciliation/run` | — | Run reconciliation |
| GET | `/v1/reconciliation/discrepancies` | — | List discrepancies |
| POST | `/v1/reconciliation/discrepancies/:id/resolve` | — | Resolve discrepancy |
| POST | `/v1/accounts` | — | Create account config |
| GET | `/v1/accounts` | — | List accounts |
| POST | `/v1/webhooks` | — | Create webhook endpoint |
| GET | `/v1/webhooks` | — | List webhook endpoints |
| DELETE | `/v1/webhooks/:id` | — | Delete webhook endpoint |
| GET | `/v1/healthz` | — | Liveness probe |
| GET | `/v1/readyz` | — | Readiness probe |
| GET | `/v1/metrics` | — | Prometheus metrics |
| GET | `/v1/admin/chaos` | — | Get chaos config |
| PUT | `/v1/admin/chaos` | — | Set chaos config |
| POST | `/webhooks/stripe` | — | Stripe inbound webhook (signature verified) |

### Events — Published (exchange `payments.x`)

| Event | Routing Key | Description |
|-------|-------------|-------------|
| `payment.intent.created` | `payment.intent.created` | Payment intent created |
| `payment.authorized` | `payment.authorized` | Payment authorized |
| `payment.settled` | `payment.settled` | Ledger entry posted; consumed by orders |
| `payment.refunded` | `payment.refunded` | Refund processed |
| `reconciliation.discrepancy_found` | `reconciliation.discrepancy_found` | Reconciliation found issues |

### Events — Consumed

| Source | Event(s) | Condition | Description |
|--------|----------|------------|-------------|
| node-b2b-orders | `payment.charge_requested`, `order.confirmed` | `ORDERS_INTEGRATION_ENABLED=true` | Creates payment intent when order confirmed |
| spring-saas-core | `tenant.created`, `tenant.updated`, `tenant.deleted` | `SAAS_INTEGRATION_ENABLED=true` | Syncs tenant metadata |

### Headers

See [headers.md](headers.md).

| Header | Required | Notes |
|--------|----------|-------|
| `Authorization` | Yes | Bearer JWT (spring-saas-core or local) |
| `X-Tenant-Id` | Yes | Must match JWT `tid` (or `*` for global admins) |
| `Content-Type` | Yes (on body) | `application/json` |
| `Idempotency-Key` | Conditional | Required on POST payment-intents, confirm, void |
| `X-Correlation-Id` | Optional | Auto-generated if absent |

### Identity (JWT)

See [identity.md](identity.md). Validates JWT from spring-saas-core or issues locally via `POST /v1/auth/token`. Supports HS256 (dev) and RS256/JWKS (production).

---

## Changelog format

For each release that introduces breaking changes, add an entry:

```markdown
## [X.Y.Z] - YYYY-MM-DD

### BREAKING

- **REST:** Description of change. Migration: …
- **Events:** Description. Migration: …

### Added

- Non-breaking additions.
```

---

## Release history

### [1.0.0] - Baseline

Initial v1 contract surface. All endpoints and events listed above are the baseline. No breaking changes recorded.
