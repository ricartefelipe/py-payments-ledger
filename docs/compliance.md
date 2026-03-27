# Compliance — py-payments-ledger

## Audit Log

### O que é auditado

| Categoria | Ações | Detalhes |
|---|---|---|
| **Autenticação** | `auth.login.success`, `auth.login.failed` | Registra tentativas de login (sucesso e falha) com e-mail do ator |
| **Autorização** | `authz.denied` | Toda negativa ABAC/RBAC é auditada com motivo (`missing_permission`, `no_policy`, `policy_deny`, `plan_not_allowed`, `region_not_allowed`) |
| **Pagamentos** | `payment_intent.created`, `payment_intent.confirmed`, `payment_intent.settled` | Ciclo de vida completo do payment intent com valores e moeda |
| **Reembolsos** | `refund.created` | Criação de reembolso com valor, status e payment intent associado |
| **Webhooks** | `webhook_endpoint.created`, `webhook_endpoint.deleted` | Gestão de endpoints de webhook por tenant |
| **Reconciliação** | `reconciliation.run`, `discrepancy.resolved` | Execução de reconciliação e resolução de discrepâncias |

### Estrutura do registro

Cada entrada de audit contém:

- `id` — UUID único
- `tenant_id` — identificador do tenant (nullable para ações globais)
- `actor_sub` — subject do ator (e-mail do usuário ou `system`/`worker`)
- `action` — ação executada (formato: `domínio.ação`)
- `target` — recurso alvo (formato: `tipo:id` ou path)
- `detail` — JSON com contexto adicional
- `correlation_id` — ID de correlação para rastreamento distribuído
- `created_at` — timestamp UTC

## Política de retenção

- Configurável via variável de ambiente `AUDIT_RETENTION_DAYS` (padrão: **90 dias**)
- Registros mais antigos que o período configurado podem ser removidos por job de limpeza agendado
- Recomendação: exportar registros antes da remoção para armazenamento de longo prazo

## Exportação

- **Endpoint**: `GET /v1/audit/export`
- **Autenticação**: requer permissão `audit:read`
- **Filtros**: `start_date`, `end_date` (ISO 8601)
- **Formato**: JSON array
- **Limite**: 10.000 registros por exportação
- **Header**: `Content-Disposition: attachment; filename=audit_export.json`

Para consulta paginada, usar `GET /v1/audit` com cursor-based pagination.

## Considerações de PII

- **Não armazenado**: números de cartão, CVVs, dados bancários completos
- **Armazenamento mínimo**: apenas e-mail do ator (`actor_sub`) e referências de cliente (`customer_ref`)
- **Sem dados sensíveis em detail**: o campo `detail` contém apenas metadados operacionais (valores, moedas, status, motivos de negativa)
- **Gateway refs**: referências opacas do gateway de pagamento, sem dados de cartão

## Residência de dados

- A residência de dados segue a configuração de região do tenant (`region` no JWT/tenant config)
- O campo `tenant_id` em cada registro de audit permite filtragem por tenant
- Em implantações multi-região, cada instância do serviço atende apenas tenants da sua região configurada
- Políticas ABAC com `allowed_regions` garantem que operações respeitem as restrições de região

## Consulta de audit logs

### Listagem com filtros

```
GET /v1/audit?action=auth.login.failed&start_date=2025-01-01T00:00:00Z&limit=50
Authorization: Bearer <token>
X-Tenant-Id: <tenant_id>
```

### Parâmetros de filtro

| Parâmetro | Tipo | Descrição |
|---|---|---|
| `action` | string | Filtrar por ação específica |
| `actor_sub` | string | Filtrar por ator |
| `start_date` | datetime | Data inicial (ISO 8601) |
| `end_date` | datetime | Data final (ISO 8601) |
| `cursor` | string | Token opaco para continuar listagens paginadas |
| `limit` | int | Itens por página (1–200, padrão 50) |
