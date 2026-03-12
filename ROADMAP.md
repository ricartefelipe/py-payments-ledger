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
- [x] Fraud analytics, ledger anomalies e cashflow forecast (IA/LLM)
- [x] Distributed tracing (OpenTelemetry/Jaeger)
- [x] Circuit breaker para chamadas ao gateway
- [x] Encryption at rest (AES-256-GCM) para dados sensíveis

## v2.0 (Next)
- [ ] Tokenized card storage (PCI Level 1 — vault próprio ou delegado ao gateway)
