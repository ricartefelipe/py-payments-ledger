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

1. **Signature** — HS256, verified against `JWT_SECRET`.
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
| `JWT_SECRET` | `change-me` | Shared secret for HS256 verification |
| `JWT_ISSUER` | `local-auth` | Expected `iss` claim value |
| `TOKEN_EXPIRES_SECONDS` | `3600` | Token lifetime in seconds (local issuance) |

In production, use the **same** `JWT_SECRET` and `JWT_ISSUER` configured in spring-saas-core.
