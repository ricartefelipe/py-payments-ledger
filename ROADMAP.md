# Roadmap — py-payments-ledger

## Fase 1 — MVP Técnico (atual)

- [x] Multi-tenant via `X-Tenant-Id`
- [x] JWT HS256 + RBAC/ABAC via policies em DB
- [x] Rate limiting distribuído (Redis token bucket Lua)
- [x] Outbox pattern + worker + DLQ
- [x] Idempotência (`Idempotency-Key`) para create e confirm
- [x] Ledger double-entry automático com endpoint de balances
- [x] Auditoria de login (sucesso/falha) e denies de autorização
- [x] Integração com orders (`order.confirmed` → payment intent → `payment.settled`)
- [x] Chaos middleware por tenant
- [x] Observabilidade: Prometheus metrics + Grafana dashboards
- [x] Docker Compose full-stack (API, worker, Postgres, Redis, RabbitMQ)
- [x] Scripts operacionais: up, migrate, seed, smoke, api-export

## Fase 2 — Hardening & Testes

- [ ] Cobertura de testes ≥ 80% (unit + integration)
- [ ] Testes de integração com Testcontainers (Postgres, Redis, RabbitMQ)
- [ ] Contract tests para eventos RabbitMQ (producer/consumer)
- [ ] Load testing com Locust ou k6
- [ ] Migrar JWT HS256 → RS256 (chaves assimétricas)
- [ ] Structured logging unificado (OpenTelemetry)
- [ ] Healthcheck detalhado com status de dependências
- [ ] CI/CD pipeline completo com gates de qualidade

## Fase 3 — Funcionalidades de Produto

- [ ] Webhooks de notificação para clientes (payment.settled, payment.failed)
- [ ] Suporte a refunds (estorno parcial/total com ledger reversal)
- [ ] Multi-currency com câmbio configurável
- [ ] Reconciliação automática entre ledger e gateway externo
- [ ] Dashboard administrativo (Backoffice UI)
- [ ] Relatórios financeiros exportáveis (CSV/PDF)

## Fase 4 — Escala & SaaS

- [ ] API keys por tenant (substituindo JWT para integrações M2M)
- [ ] Billing por uso (metering de API calls)
- [ ] Sharding de dados por tenant (schema-per-tenant ou row-level)
- [ ] Read replicas para queries de relatório
- [ ] Cache de consultas frequentes (Redis)
- [ ] Feature flags dinâmicos via painel admin
- [ ] SDK clientes (Python, Node, Go)
