# Identity Contract — JWT

py-payments-ledger **validates** JWT tokens issued by **spring-saas-core** (or a compatible issuer). It can also issue tokens locally for development via `POST /v1/auth/token`.

## Token format

| Field | Type | Description |
|-------|------|-------------|
| `sub` | string | User identifier (email) |
| `tid` | string | Tenant identifier (must match `X-Tenant-Id` header); `*` for global admins |
| `roles` | string[] | User roles (e.g. `admin`, `ops`, `sales`) |
| `perms` | string[] | Granted permissions (e.g. `payments:write`, `ledger:read`) |
| `plan` | string | Tenant subscription plan (e.g. `free`, `pro`, `enterprise`) |
| `region` | string | Tenant region code (e.g. `region-a`, `br-south`) |
| `jti` | string | Unique token identifier (`{user_id}.{issued_at}`) |
| `ctx` | object | Additional context (e.g. `{"email": "ops@demo.example.com"}`) |
| `iss` | string | Token issuer — must match `JWT_ISSUER` env var |
| `exp` | number | Expiration timestamp (Unix epoch seconds) |
| `iat` | number | Issued-at timestamp (Unix epoch seconds) |

## Validation rules

1. **Signature** — HS256 (dev, via `JWT_SECRET`) or RS256 (production, via JWKS endpoint at `JWKS_URI`).
2. **Issuer** — `iss` must equal the configured `JWT_ISSUER` (default `local-auth`).
3. **Expiration** — Token must not be expired (`exp > now`).
4. **Tenant match** — `tid` claim must match the `X-Tenant-Id` request header (unless `tid` is `*` for global admins).
5. **Permissions** — Route-specific permissions checked via `require_permission()` against the `perms` claim.
6. **ABAC** — Attribute-based policies evaluated using `plan`, `region`, and the `Policy` table (effect, allowed_plans, allowed_regions).

## Example decoded payload

```json
{
  "sub": "ops@demo.example.com",
  "tid": "tenant_demo",
  "roles": ["ops"],
  "perms": ["payments:write", "payments:read", "ledger:read"],
  "plan": "pro",
  "region": "br-south",
  "jti": "a1b2c3d4.1709500000",
  "ctx": {"email": "ops@demo.example.com"},
  "iss": "spring-saas-core",
  "iat": 1709500000,
  "exp": 1709503600
}
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `JWT_SECRET` | `change-me` | Shared secret for HS256 verification (dev only) |
| `JWT_ISSUER` | `local-auth` | Expected `iss` claim value |
| `JWT_ALGORITHM` | `HS256` | Algorithm hint (`HS256` or `RS256`); auto-detected from `JWKS_URI` |
| `JWKS_URI` | *(empty)* | JWKS endpoint URL for RS256 validation; when set, `JWT_SECRET` is ignored for token verification |
| `TOKEN_EXPIRES_SECONDS` | `3600` | Token lifetime in seconds (local issuance) |

### Dev mode (default)

Use `JWT_SECRET` + `JWT_ISSUER` matching spring-saas-core. `JWKS_URI` left empty.

### Production (OIDC/RS256)

Set `JWKS_URI` to your identity provider's JWKS endpoint (e.g. `https://idp.example.com/.well-known/jwks.json`). Tokens are validated using the RS256 public key fetched from JWKS. `JWT_SECRET` is not required for validation but still used for local token issuance if enabled.

---

## Tenant context extraction

1. **HTTP requests** — The `Authorization: Bearer <token>` header provides the JWT. The `get_principal()` dependency decodes the token and builds a `Principal` with `sub`, `tid`, `roles`, `perms`, `plan`, `region`. The `enforce_tenant()` dependency validates that `X-Tenant-Id` matches the `tid` claim (or allows any tenant when `tid` is `*` for global admins). The resolved tenant ID is stored in context via `set_tenant_id()` for use in downstream handlers.
2. **Worker / event consumption** — When consuming RabbitMQ messages (e.g. from orders or saas-core), tenant context is extracted from message headers (`X-Tenant-Id`) or payload (`tenant_id`). The worker sets `set_tenant_id()` and `set_subject()` before processing.

---

## Service identity in events

When py-payments-ledger publishes domain events (via outbox to RabbitMQ), each event includes:

| Field | Description |
|-------|-------------|
| `tenant_id` | Tenant context for the operation; propagated to consumers |
| `correlation_id` | Distributed tracing ID (from request or auto-generated) |
| `aggregateType` | Aggregate type (e.g. `payment`, `invoice`) |
| `aggregateId` | ID of the affected aggregate |

The service name `py-payments-ledger` is the source of events on the `payments.x` exchange. Consuming services (e.g. node-b2b-orders) use `tenant_id` and `correlation_id` for tenant scoping and traceability. See [docs/contracts/events.md](events.md) for full event schemas.
