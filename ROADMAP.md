# Roadmap — py-payments-ledger

## v1.0 (Released — 2026-03-07)
- [x] Payment intents (create, confirm, settle)
- [x] Double-entry ledger
- [x] Stripe integration
- [x] Outbox pattern + RabbitMQ
- [x] Multi-tenant with JWT
- [x] Idempotency

## v1.1 (Released — 2026-03-12)
- [x] Webhook outbound delivery (HTTP)
- [x] Reconciliation automation (Stripe auto-fix)
- [x] Payment retry with exponential backoff
- [x] Payment void/cancel for authorized payments
- [x] Invoice generation with full lifecycle
- [x] Recurring charges with automatic billing cycle
- [x] Stripe capture before settlement + inbound webhooks
- [x] Multi-gateway per tenant (routing by currency/payment type)
- [x] Failure notifications (retry exhaustion events)

## v1.2 (Released — 2026-03-12)
- [x] Multi-currency ledger accounts (conversão automática, taxas de câmbio)
- [x] Payout management (transferências para sellers/fornecedores)
- [x] Split payments (divisão de pagamento entre múltiplos recebedores)
- [x] Dispute/chargeback handling (contestações via gateway)
- [x] Payment links (geração de links de pagamento one-time)

## v1.3 (Released — 2026-03-12)
- [x] Additional payment gateways (PagSeguro, Mercado Pago) com webhooks dedicados
- [x] Subscription/recurring management via API (ciclo de cobrança automático)
- [x] Advanced reporting (relatórios financeiros, export CSV/JSON de auditoria)
- [x] Real-time payment notifications (SSE via event broadcaster)
- [x] Fraud analytics, ledger anomalies e cashflow forecast
- [x] Distributed tracing (OpenTelemetry/Jaeger)
- [x] Circuit breaker para chamadas ao gateway
- [x] Encryption at rest (AES-256-GCM) para dados sensíveis

## v2.0 (Released — 2026-03-12)
- [x] Tokenized card storage (gateway-delegated — Stripe, PagSeguro, Mercado Pago)
- [x] API de métodos de pagamento salvos (CRUD por tenant/customer)
- [x] Pagamento com token salvo (authorize com payment_method_token)

## v2.0.0 — Integridade financeira (Released — 2026-10-29)
- [x] Índice cego para busca em colunas cifradas (webhook, reconciliação, dedupe de pedido)
- [x] Journal append-only no banco, dedupe por fato e reversão vinculada
- [x] Equilíbrio contábil por moeda
- [x] Finalização única convergente (HTTP, consumer, webhook, reconciliação)
- [x] Gateway fora do lock, com claim durável e CAS por versão
- [x] Resultado incerto como estado próprio, resolvido por consulta
- [x] Inbox de webhook com unicidade por `(provider, event_id)`
- [x] CI com PostgreSQL/Redis/RabbitMQ e integração obrigatória
- [x] Promoção automática de branch substituída por verificação somente leitura

## Adiado com motivo (P2/P3)
- [ ] P2 — Reprocessamento automático da fila de revisão (hoje é chamada explícita).
      Depende de política de retenção de chave do provedor por endpoint.
- [ ] P2 — Painel de backoffice com aprovação para operações sensíveis.
      Sem segundo caso de uso que justifique a interface.
- [ ] P2 — Exportação contábil em formato de terceiros (SPED/CSV padronizado).
      Exige validação por especialista contábil antes de prometer formato.
- [ ] P3 — Event sourcing completo. O journal append-only já dá a trilha
      necessária; o custo de reescrita não se justifica.
- [ ] P3 — Armazenar PAN/CVV. Fora de escopo por desenho: a tokenização é
      delegada ao provedor.
