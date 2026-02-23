# Alterações para Pronto para Venda

## Lista de arquivos mudados/criados

| Path | Motivo |
|------|--------|
| `.gitignore` | `.idea/` já estava; mantido |
| `.env.example` | ORDERS_ROUTING_KEYS, CORS_ORIGINS, ORDERS_INTEGRATION_ENABLED=true |
| `README.md` | URL clone (ricartefelipe), Quick start, arquitetura, eventos, segurança, scripts |
| `pyproject.toml` | ruff exclude .idea |
| `scripts/up.sh` | Inclui migrate após compose up |
| `scripts/lint.sh` | **Novo** — ruff, black check, mypy |
| `scripts/format.sh` | **Novo** — black, ruff --fix |
| `scripts/publish_charge_request.py` | **Novo** — publicar payment.charge_requested para smoke |
| `scripts/smoke.sh` | Passo 11 (Idempotency-Key 400), passo 12 (charge_requested) |
| `src/shared/config.py` | orders_routing_keys, cors_origins |
| `src/api/main.py` | CORS condicional (local=*, prod=allowlist) |
| `src/api/middlewares.py` | Chaos apenas em APP_ENV=local |
| `src/api/routers/payments.py` | Idempotency-Key obrigatório em create |
| `src/application/payments.py` | payment.settled com order_id, tenant_id, correlation_id |
| `src/application/outbox.py` | count_pending() para /metrics |
| `src/api/routers/metrics.py` | Atualiza outbox_events_pending em /metrics |
| `src/shared/metrics.py` | OUTBOX_PENDING_GAUGE |
| `src/infrastructure/mq/rabbit.py` | declare_external_queue_multi_bind |
| `src/worker/main.py` | orders_routing_keys, multi-bind |
| `src/worker/handlers/payments.py` | payment.charge_requested, order.confirmed, parse resiliente |
| `src/worker/handlers/charge_request.py` | **Novo** — parse_charge_payload (camel/snake) |
| `docker/api.Dockerfile` | Multi-stage, non-root, healthcheck |
| `docker/worker.Dockerfile` | Multi-stage, non-root |
| `tests/conftest.py` | **Novo** — env defaults |
| `tests/unit/test_charge_request.py` | **Novo** — parse payload |
| `tests/unit/test_payments.py` | **Novo** — create/confirm |
| `tests/unit/test_outbox.py` | **Novo** — claim, mark_sent, mark_failed |
| `tests/unit/test_jwt.py` | orders_routing_keys, cors_origins em Settings |
| `docs/contracts/events.md` | **Novo** — contratos e exemplos JSON |
| `docs/DEMO.md` | **Novo** — demonstração 3 min |
| `.idea/*` | Removido do tracking git |

## Comandos que devem passar

```bash
./scripts/up.sh && ./scripts/seed.sh && ./scripts/smoke.sh
python3 -m ruff check .
python3 -m black --check .
python3 -m pytest tests/ -v
```

## Demonstração 3 minutos

Ver [docs/DEMO.md](DEMO.md).
