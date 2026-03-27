# Prompt de Evolução — py-payments-ledger

Este documento define o **prompt de evolução** do projeto. Use-o como contexto em decisões de arquitetura, backlog e evolução contínua.

---

## Identidade

- **py-payments-ledger** = Motor de Pagamentos com Ledger Contábil da plataforma Fluxe.
- Gerencia: processamento de pagamentos (charge, refund, settle), ledger de partidas dobradas, reconciliação, webhooks, relatórios financeiros.
- Consome JWT emitido pelo **spring-saas-core** e aplica ABAC/RBAC localmente; integra-se com **node-b2b-orders** via eventos e com saas-core via tenant events.

---

## Objetivo: entregável e vendável

Priorizar evolução que aproxime o projeto destes critérios:

| Área | Critério de "pronto para venda" |
|------|----------------------------------|
| **Funcional** | Pagamentos (charge/refund/settle); ledger partidas dobradas; reconciliação automática; webhooks; relatórios financeiros; integração com orders e saas-core. |
| **Segurança** | ABAC/RBAC com DENY precedente, default-deny; JWT validado via spring-saas-core; auditoria de ACCESS_DENIED; gateway credentials seguras; sem credenciais em código. |
| **Operacional** | Health, Prometheus, OpenAPI, scripts up/migrate/seed/smoke, deploy reproduzível, chaos engineering. |
| **Contratos** | Documentação de identidade/headers/eventos alinhada com saas-core e orders; API v1 estável. |
| **Compliance** | Auditoria de ações sensíveis e negações; retenção/exportação de audit log; reconciliação auditável. |

Preservar sempre: multi-tenancy, ABAC, ledger contábil, integração com Fluxe B2B Suite + saas-core + orders.

---

## Critérios detalhados

### Funcional

- Processamento de pagamentos: charge, refund, settle via gateway (Stripe / fake)
- Ledger de partidas dobradas (LedgerEntry + LedgerLine com DEBIT/CREDIT)
- Reconciliação automática com detecção de discrepâncias
- Webhooks para integradores externos (delivery via httpx)
- Relatórios financeiros: receita, saldos por conta
- Idempotência via header `Idempotency-Key` com cache Redis
- Outbox confiável para eventos de domínio (worker RabbitMQ)
- Integração com orders (consome `payment.charge_requested`, `order.confirmed`)
- Integração com saas-core (consome tenant events: created, updated, deleted)
- Auditoria consultável via `GET /v1/audit` com filtros e exportação

### Segurança

- ABAC com regra DENY precedente e default-deny (authorize + Policy)
- RBAC via permissões no JWT
- JWT validado (claims: sub, tid, roles, perms, plan, region, iss, exp)
- Auditoria de todas as tentativas ACCESS_DENIED
- Gateway credentials (STRIPE_API_KEY) via variáveis de ambiente, nunca em código
- Sem credenciais hardcoded

### Operacional

- Health checks: `/v1/healthz`, `/v1/readyz` (DB + Redis + RabbitMQ)
- Métricas Prometheus (`/v1/metrics`)
- OpenAPI (`/openapi.json` + Swagger UI em `/docs`)
- Scripts: `up.sh`, `migrate.sh`, `seed.sh`, `smoke.sh`
- Docker multi-stage (api + worker)
- Chaos engineering configurável via `/v1/admin/chaos`

### Contratos

- Documentação de eventos (`docs/contracts/events.md`)
- Documentação de identidade JWT (`docs/contracts/identity.md`) — a criar
- Documentação de headers (`docs/contracts/headers.md`) — a criar
- API v1 estável

### Compliance

- Auditoria de ações sensíveis (pagamentos, refunds, ajustes de ledger)
- Auditoria de negações (ACCESS_DENIED)
- Reconciliação auditável com relatório de discrepâncias
- Retenção e exportação de audit log (ver `docs/compliance.md`)

---

## IA/LLM no serviço

O projeto deve habilitar uso de IA/LLM para:

- Detecção de fraude em pagamentos
- Análise de padrões de reconciliação e discrepâncias recorrentes
- Previsão de fluxo de caixa
- Detecção de anomalias no ledger contábil
- Documentação viva

### Requisitos para IA/LLM

- Dados para aprendizado (agregados, sem PII): histórico de transações, relatórios de reconciliação, saldos
- APIs estáveis para agentes: `/v1/payments`, `/v1/reports/revenue`, `/v1/reports/account-balances`, `/v1/audit`
- Eventos documentados (`docs/contracts/events.md`)
- Segurança: ABAC, sem PII desnecessário, auditoria de acesso

---

## Assistentes de código no repositório

- **Desenvolvimento:** ao sugerir ou implementar mudanças, priorizar itens que aproximem dos critérios acima e habilitem IA/LLM.
- **Revisão de arquitetura:** garantir que novas features não quebrem contratos com spring-saas-core e node-b2b-orders.
- **Detalhes:** critérios finos e diretrizes técnicas estão neste documento e em `docs/BACKLOG-EVOLUCAO.md`.
- **Compliance:** auditoria, retenção e exportação em `docs/compliance.md`.

---

## Resumo em uma frase

O py-payments-ledger é o Motor de Pagamentos com Ledger Contábil da plataforma Fluxe, pronto para venda quando cumprir os critérios funcionais, de segurança, operacional, contratos e compliance acima, com suporte à integração com agentes IA/LLM.
