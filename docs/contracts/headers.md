# HTTP Headers Contract

Especificação dos headers HTTP obrigatórios e opcionais para integração com spring-saas-core, node-b2b-orders e py-payments-ledger.

---

## Required and conditional headers

| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | Yes | Bearer token: `Bearer <JWT>` |
| `X-Tenant-Id` | Yes | Tenant ID; must match `tid` claim in JWT (except for global admins per policies) |
| `X-Correlation-Id` | Optional | Request tracing ID; generated automatically if absent |
| `Idempotency-Key` | Conditional | Required on POST mutations in orders and payments (idempotent operations) |
| `Content-Type` | Yes | `application/json` for request/response bodies |

---

## Authorization

```
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

- Obrigatório em todos os endpoints protegidos
- Token deve ser válido (assinatura, expiração, issuer)
- Sem token válido → `401 Unauthorized`

---

## X-Tenant-Id

```
X-Tenant-Id: 550e8400-e29b-41d4-a716-446655440000
```

- ID do tenant no contexto da requisição
- Deve corresponder à claim `tid` do JWT (exceto quando políticas permitirem acesso cross-tenant para admins)
- Mismatch → `403 Forbidden` (tenant mismatch)

---

## X-Correlation-Id

```
X-Correlation-Id: a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

- UUID para rastreamento distribuído da requisição
- Se ausente, o serviço gera e propaga o valor na cadeia de chamadas
- Incluído em logs e auditoria para facilitar troubleshooting

---

## Idempotency-Key

```
Idempotency-Key: unique-key-per-operation-12345
```

- **Onde:** POST em mutações em node-b2b-orders e py-payments-ledger (ex.: criar pedido, cobrança)
- **Objetivo:** Evitar duplicação em retentativas; a mesma chave retorna o mesmo resultado
- Gerado pelo cliente (UUID ou hash do payload)
- Não obrigatório em spring-saas-core, mas recomendado para operações sensíveis

---

## Content-Type

```
Content-Type: application/json
```

- Obrigatório em requisições com body (POST, PATCH, PUT)
- Respostas JSON são retornadas com `Content-Type: application/json`

---

## Summary

| Service | Authorization | X-Tenant-Id | X-Correlation-Id | Idempotency-Key |
|---------|---------------|-------------|------------------|-----------------|
| spring-saas-core | Yes | Yes* | Optional | Optional |
| node-b2b-orders | Yes | Yes* | Optional | Conditional (POST mutations) |
| py-payments-ledger | Yes | Yes* | Optional | Conditional (POST mutations) |

\* Validado contra claim `tid` do JWT; exceções conforme políticas ABAC.
