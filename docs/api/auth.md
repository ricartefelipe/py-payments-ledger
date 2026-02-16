# Auth

## Login (demo)
`POST /v1/auth/token`

Body:
```json
{ "email": "admin@local", "password": "admin123", "tenantId": "tenant_demo" }
```

Returns:
```json
{ "access_token": "...", "token_type": "Bearer", "expires_in": 3600 }
```

## Claims
- `sub`: email
- `tid`: tenant id or `"*"` for global admin
- `roles`: list
- `perms`: derived from roles
- `plan`, `region`: from tenant
- `jti`: unique token id
