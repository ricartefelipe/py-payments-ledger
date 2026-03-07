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
- [ ] Suporte a múltiplos gateways simultâneos por tenant
- [ ] Notificações de falha de pagamento (retry exhaustion)

---

## Segurança

- [x] ABAC com DENY precedente e default-deny (authorize + Policy)
- [x] RBAC via permissões no JWT
- [x] Auditoria de ACCESS_DENIED
- [x] JWT validado (sub, tid, roles, perms, plan, region)
- [x] Gateway credentials via env vars (nunca em código)
- [x] Sem credenciais hardcoded
- [x] Rate limiting por tenant/usuário (token bucket via Redis)
- [ ] Rotação de JWT_SECRET sem downtime
- [ ] OIDC/RS256 para produção (depende do spring-saas-core)
- [ ] Criptografia de dados sensíveis de pagamento em repouso

---

## Operacional

- [x] Health checks (/v1/healthz, /v1/readyz com DB + Redis + RabbitMQ)
- [x] Métricas Prometheus (/v1/metrics)
- [x] OpenAPI (/openapi.json + Swagger UI)
- [x] Docker multi-stage (api + worker)
- [x] Scripts: up.sh, migrate.sh, seed.sh, smoke.sh
- [x] Chaos engineering (/v1/admin/chaos)
- [ ] Alertas Grafana pré-configurados
- [ ] Structured logging (JSON) em produção
- [ ] Circuit breaker para chamadas ao gateway de pagamento

---

## Contratos

- [x] docs/contracts/events.md
- [ ] docs/contracts/identity.md
- [ ] docs/contracts/headers.md
- [x] API v1 estável
- [ ] Versionamento de contratos (changelog de breaking changes)

---

## Compliance

- [x] Auditoria de ações sensíveis (pagamentos, refunds, ajustes de ledger)
- [x] Auditoria de negações (ACCESS_DENIED)
- [x] Reconciliação auditável com relatório de discrepâncias
- [x] docs/compliance.md
- [ ] Retenção configurável de audit log (TTL/archival)
- [ ] Política de privacidade de dados (PCI-DSS awareness)
- [ ] Exportação de audit log (CSV/JSON)

---

## IA/LLM

- [ ] API de dados agregados para análise de fraude
- [ ] Endpoint de anomalias no ledger
- [ ] Previsão de fluxo de caixa via dados de transações
- [ ] Documentação viva gerada por IA
