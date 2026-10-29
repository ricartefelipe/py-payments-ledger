# JWT Identity Contract

Contracto de identidade JWT compartilhado entre **spring-saas-core**, **node-b2b-orders** e **py-payments-ledger**. A identidade é emitida ou delegada pelo control plane (spring-saas-core) e validada por todos os serviços da plataforma B2B.

---

## Overview

O JWT contém claims padronizadas que permitem autorização multi-tenant consistente em todos os backends. Cada serviço valida o token e aplica as mesmas regras ABAC/RBAC baseadas em `tid`, `plan`, `region`, `roles` e `perms`.

---

## JWT Claims

| Claim | Type | Description |
|-------|------|-------------|
| `sub` | string | User identifier (email or OIDC subject) |
| `tid` | string | Tenant identifier (UUID) |
| `roles` | string[] | User roles (e.g., admin, ops, sales) |
| `perms` | string[] | Permissions (e.g., tenants:read, orders:write) |
| `plan` | string | Tenant plan (free, pro, enterprise) |
| `region` | string | Tenant region (e.g., region-a, region-b) |
| `iss` | string | Issuer (spring-saas-core or OIDC provider) |
| `exp` | number | Expiration timestamp (Unix seconds) |

### Standard claims (optional)

| Claim | Type | Description |
|-------|------|-------------|
| `iat` | number | Issued at timestamp |
| `aud` | string | Audience (e.g., spring-saas-core) |

---

## Example payload

```json
{
  "sub": "user@example.com",
  "tid": "550e8400-e29b-41d4-a716-446655440000",
  "roles": ["admin", "ops"],
  "perms": ["tenants:read", "tenants:write", "orders:write"],
  "plan": "enterprise",
  "region": "us-east-1",
  "iss": "spring-saas-core",
  "exp": 1709650800
}
```

---

## Signing and validation

### Development (HS256)

- **Profile:** `local` or `AUTH_MODE=hs256`
- **Signing:** HS256 with shared `JWT_SECRET` (or `JWT_HS256_SECRET`)
- **Config:** Todos os serviços devem usar o mesmo segredo em ambiente de desenvolvimento para validar tokens emitidos pelo spring-saas-core ou gerados via `POST /v1/dev/token`
- **Security:** Use apenas em ambientes locais ou de integração; nunca em produção

### Production (OIDC / RS256)

- **Profile:** `prod` or `AUTH_MODE=oidc`
- **Signing:** RS256 via OIDC provider (e.g., Keycloak)
- **Validation:** Issuer validation via `OIDC_ISSUER_URI`; chaves públicas via JWKS (`OIDC_JWK_SET_URI`)
- **Claims mapping:** O provider OIDC deve mapear ou incluir as claims `tid`, `roles`, `perms`, `plan`, `region` no token (ou o spring-saas-core pode enriquecer via token exchange/delegation)

---

## Validation per service

### spring-saas-core

- **HS256:** Valida assinatura com `JWT_HS256_SECRET`; verifica `iss` (default: `spring-saas-core`) e `exp`
- **OIDC:** Resolve JWKS do issuer; valida assinatura RS256, issuer e audience; extrai claims customizadas (`tid`, `roles`, `perms`, `plan`, `region`)

### node-b2b-orders

- Usa o mesmo contrato de claims
- Valida JWT com o mesmo segredo (HS256) ou issuer OIDC (RS256)
- Valida `X-Tenant-Id` contra claim `tid`
- Avalia políticas ABAC com base em `plan`, `region`, `perms`

### py-payments-ledger

- Usa o mesmo contrato de claims
- Valida JWT com o mesmo segredo (HS256) ou issuer OIDC (RS256)
- Valida `X-Tenant-Id` contra claim `tid`
- Aplica regras de tenant e permissão para operações de pagamento

---

## Acesso cross-tenant (`tid: "*"`)

Um token com `tid: "*"` opera sobre **qualquer** tenant — inclusive sobre o
dinheiro de todos eles. Historicamente esse privilégio era concedido apenas
pela presença do claim, sem permissão correspondente e sem registro de uso.

O `py-payments-ledger` passou a tratar isso em duas etapas:

| Etapa | Comportamento |
|---|---|
| Hoje (`CROSS_TENANT_REQUIRE_PERMISSION=false`, default) | O claim continua suficiente. Cada acesso a outro tenant é registrado em `WARNING` com ator, tenant alvo e papéis. Tokens sem a permissão geram um aviso adicional informando que serão recusados quando a exigência for ligada. |
| Depois (`CROSS_TENANT_REQUIRE_PERMISSION=true`) | O claim deixa de bastar: é exigida também a permissão `tenant:any` em `perms`. Sem ela, a resposta é `403`. |

### O que o emissor precisa passar a incluir

**Mudança necessária no `spring-saas-core`** (ou no provider OIDC que enriquece
as claims): incluir `tenant:any` em `perms` para os principais que realmente
devem operar cross-tenant.

```json
{
  "tid": "*",
  "roles": ["admin"],
  "perms": ["payments:read", "payments:write", "tenant:any"]
}
```

A permissão já existe no seed deste serviço (concedida ao papel `admin`, política
restrita ao plano `enterprise`), portanto o lado do ledger está pronto. A ordem
recomendada da transição:

1. o emissor passa a incluir `tenant:any` nos tokens administrativos;
2. os logs do ledger deixam de registrar o aviso de permissão ausente;
3. só então `CROSS_TENANT_REQUIRE_PERMISSION=true` é ligado.

Ligar a exigência antes do passo 1 recusa os tokens administrativos em uso.

---

## Token generation (dev only)

`POST /v1/dev/token` (profile `local`) aceita body JSON:

```json
{
  "sub": "user@example.com",
  "tid": "uuid-do-tenant",
  "roles": ["admin"],
  "perms": ["tenants:read", "tenants:write"],
  "plan": "enterprise",
  "region": "us-east-1"
}
```

Retorna um JWT HS256 assinado com claims preenchidas.

---

## Cross-service consistency

| Aspect | Requirement |
|--------|-------------|
| Secret / issuer | HS256: mesmo `JWT_HS256_SECRET` em todos os serviços. OIDC: mesmo issuer e audience |
| Claims | `sub`, `tid`, `roles`, `perms`, `plan`, `region` obrigatórios |
| Header | `Authorization: Bearer <token>` em todas as requisições protegidas |
| Tenant header | `X-Tenant-Id` deve coincidir com `tid` (exceto para admins globais conforme políticas) |
