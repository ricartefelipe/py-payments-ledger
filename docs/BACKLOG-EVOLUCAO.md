# Backlog de Evolução

Estado atual por critério de "pronto para venda".

---

## Funcional

- [x] Processamento de pagamentos (charge, refund, settle)
- [x] Ledger de partidas dobradas (LedgerEntry + LedgerLine DEBIT/CREDIT)
- [x] Reconciliação automática com detecção de discrepâncias
- [x] Webhooks para integradores externos (delivery via httpx)
- [x] Relatórios financeiros (receita, saldos por conta)
- [x] Idempotência via Idempotency-Key + Redis
- [x] Outbox para eventos de domínio (worker RabbitMQ)
- [x] Integração com orders (consome charge_requested, order.confirmed)
- [x] Integração com saas-core (consome tenant events)
- [x] Auditoria consultável via GET /v1/audit
- [x] Gateway multi-provider (Stripe + fake)
- [x] Payment void/cancel para pagamentos autorizados
- [x] Reconciliação Stripe com auto-fix
- [x] Invoice generation com ciclo de vida completo
- [x] Recurring charges com ciclo de cobrança automático
- [x] Stripe capture antes do settlement e webhook inbound
- [x] Suporte a múltiplos gateways simultâneos por tenant
- [x] Notificações de falha de pagamento (retry exhaustion)
- [x] Script de seed com pagamentos, ledger, faturas e dados de 3 meses simulados

---

## Segurança

- [x] ABAC com DENY precedente e default-deny (authorize + Policy)
- [x] RBAC via permissões no JWT
- [x] Auditoria de ACCESS_DENIED
- [x] JWT validado (sub, tid, roles, perms, plan, region)
- [x] Gateway credentials via env vars (nunca em código)
- [x] Sem credenciais hardcoded
- [x] Rate limiting por tenant/usuário (token bucket via Redis)
- [x] Rotação de JWT_SECRET sem downtime
- [x] OIDC/RS256 para produção (JWT_PUBLIC_KEY ou JWKS_URI)
- [x] Criptografia de dados sensíveis de pagamento em repouso

---

## Operacional

- [x] Health checks (/v1/healthz, /v1/readyz com DB + Redis + RabbitMQ)
- [x] Métricas Prometheus (/v1/metrics)
- [x] OpenAPI (/openapi.json + Swagger UI)
- [x] Docker multi-stage (api + worker)
- [x] Scripts: up.sh, migrate.sh, seed.sh, smoke.sh
- [x] Chaos engineering (/v1/admin/chaos)
- [x] Alertas Grafana pré-configurados
- [x] Structured logging (JSON) em produção
- [x] Circuit breaker para chamadas ao gateway de pagamento
- [x] Fix template Alembic (script.py.mako) e merge de heads divergentes
- [x] Dependências python-dateutil e cryptography no requirements.txt
- [x] Migration 0012: tabela saved_payment_methods para tokenização
- [x] scripts/seed_realistic_data.py idempotente para ambientes de teste/homologação

---

## Contratos

- [x] docs/contracts/events.md
- [x] docs/contracts/identity.md
- [x] docs/contracts/headers.md
- [x] API v1 estável
- [x] Versionamento de contratos (changelog de breaking changes)

---

## Compliance

- [x] Auditoria de ações sensíveis (pagamentos, refunds, ajustes de ledger)
- [x] Auditoria de negações (ACCESS_DENIED)
- [x] Reconciliação auditável com relatório de discrepâncias
- [x] docs/compliance.md
- [x] Retenção configurável de audit log (TTL/archival)
- [x] Política de privacidade de dados (PCI-DSS awareness)
- [x] Exportação de audit log (CSV/JSON)

---

## IA/LLM

- [x] API de dados agregados para análise de fraude
- [x] Endpoint de anomalias no ledger
- [x] Previsão de fluxo de caixa via dados de transações
- [x] Documentação viva gerada por IA

---

## Tokenização (PCI)

- [x] Modelo SavedPaymentMethod (gateway_token criptografado, card_last4, brand, exp)
- [x] Port estendido com save_payment_method e delete_payment_method
- [x] Tokenização Stripe (PaymentMethod API)
- [x] Tokenização PagSeguro (card token reutilizável)
- [x] Tokenização Mercado Pago (customers/cards API)
- [x] API CRUD de métodos de pagamento salvos (/v1/payment-methods)
- [x] Pagamento com token salvo (payment_method_id no CreatePaymentIntentRequest)
- [x] Soft delete com desanexação no gateway
