# Headers Contract

All authenticated API requests to py-payments-ledger must include the headers listed below.

## Required headers

| Header | Required | Description | Example |
|--------|----------|-------------|---------|
| `Authorization` | Yes | Bearer JWT token issued by spring-saas-core (or local `/v1/auth/token`) | `Bearer eyJhbGci...` |
| `X-Tenant-Id` | Yes | Tenant identifier; must match the `tid` claim in the JWT | `tenant_demo` |
| `Content-Type` | Yes (on request body) | Media type of the request body | `application/json` |

## Conditional headers

| Header | Required on | Description | Example |
|--------|-------------|-------------|---------|
| `Idempotency-Key` | `POST /v1/payment-intents`, `POST /v1/payment-intents/:id/confirm` | Client-generated unique key to ensure at-most-once processing | `create-pi-550e8400-e29b` |

## Optional headers

| Header | Description | Default | Example |
|--------|-------------|---------|---------|
| `X-Correlation-Id` | Distributed tracing identifier propagated across services. Auto-generated (UUID) if absent. | Auto-generated | `9f3a2b1c4d5e6f7a8b9c0d1e2f3a4b5c` |

## Response headers

| Header | Description |
|--------|-------------|
| `X-Correlation-Id` | Echoed back on every response |
| `X-RateLimit-Limit` | Max requests allowed in the current window (on 429) |
| `X-RateLimit-Remaining` | Remaining requests in the current window (on 429) |
| `Retry-After` | Seconds to wait before retrying (on 429) |

## Validation behavior

- **Missing `Authorization`** → 401 Unauthorized
- **Invalid/expired JWT** → 401 Unauthorized
- **Missing `X-Tenant-Id`** → 400 Bad Request
- **`X-Tenant-Id` ≠ JWT `tid`** → 403 Forbidden
- **Missing `Idempotency-Key`** on write endpoints → 400 Bad Request
- **Duplicate `Idempotency-Key`** → Returns cached response without re-processing
- **Rate limit exceeded** → 429 Too Many Requests with `Retry-After` header
