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

## v2.0 (Next)
- [ ] Additional payment gateways (PagSeguro, Mercado Pago)
- [ ] Subscription management UI (upgrade/downgrade/cancel via API)
- [ ] Advanced reporting dashboard (BI-ready exports)
- [ ] Tokenized card storage (PCI Level 1 compliance)
- [ ] Real-time payment notifications (WebSocket/SSE)
