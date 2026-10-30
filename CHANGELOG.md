# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Nota sobre o histórico.** Este arquivo registra a 1.0.0 e a 2.0.0. As
> versões v1.1 a v1.3.1 existem como tags no repositório mas nunca tiveram
> entrada aqui, e a v2.0 do ROADMAP nunca chegou a ser marcada com tag. As
> entradas faltantes não foram reconstruídas de memória: o conteúdo seria
> aproximação, não o histórico real. O ROADMAP permanece como a referência do
> que cada versão entregou.

## [2.0.2] - 2026-10-30

### Changed
- Testes de contrato com `node-b2b-orders` executam na CI contra os schemas do produtor em ref fixa (`v1.4.2`), via `NODE_B2B_ORDERS_DIR`; schema ausente com a variável definida é falha, nunca skip.

## [2.0.1] - 2026-10-29

### Changed
- Imagens finais da API e do worker em `python:3.12-alpine` (fixada por digest), instaladas apenas por wheels (`--only-binary=:all:`); scan de imagem sem achados HIGH/CRITICAL.

### CI
- Scan da imagem publicada passa a ser bloqueante.
- Novo job `smoke-image`: constrói, escaneia e executa as imagens em runners nativos amd64 e arm64 contra PostgreSQL, Redis e RabbitMQ reais (dependências nativas, TLS, healthchecks e jornada do smoke) antes de qualquer publicação; a publicação continua condicionada à CI aprovada do mesmo SHA.

## [2.0.0] - 2026-10-29

### Fixed

- **Busca em colunas cifradas não encontrava nada em produção.** `EncryptedString`
  usa AES-256-GCM com nonce aleatório por escrita, logo `WHERE gateway_ref = :v`
  nunca casava. Com `ENCRYPTION_KEY` configurada, o webhook do Stripe, a
  reconciliação, a deduplicação de pedido do consumer e a unicidade de
  `gateway_token` falhavam em silêncio. Introduzido índice cego
  (HMAC-SHA256 com chave derivada por HKDF) nas colunas usadas em busca.
- **Pagamento podia ficar `SETTLED` sem lançamento contábil.**
  `update_payment_from_stripe_event` trocava o status e commitava, sem journal e
  sem outbox, e sem escopo de tenant nem lock. Todos os caminhos (HTTP,
  consumer, webhook, reconciliação) passam por `settlement`, que grava estado,
  lançamento e evento na mesma transação.
- **Equilíbrio contábil era verificado no total, não por moeda.** Um lançamento
  com `DEBIT 100 USD` e `CREDIT 100 BRL` era aceito.
- **Gateway era chamado com a linha do pagamento travada**
  (`SELECT ... FOR UPDATE` seguido de `asyncio.run`), em `confirm`, `capture`,
  `void` e `refund`. A chamada remota passou para fora do lock, com claim
  durável e finalização por compare-and-set.
- **Timeout era tratado como recusa definitiva**, e uma nova tentativa emitia
  operação com chave nova — caminho para cobrança dupla. Resultado incerto agora
  é um estado próprio (`UNCERTAIN`/`REVIEW`) resolvido por consulta.
- **`_audit` podia desfazer a transação do chamador.** Em caso de falha chamava
  `session.rollback()`, descartando a mudança de estado e o lançamento contábil;
  e forçava `tenant_id` para `UUID`, embora `tenants.id` seja `varchar(64)`
  desde a migração 0017, descartando a auditoria em silêncio.
- **`/readyz` devolvia a mensagem do driver** (host, porta, usuário, base) em um
  endpoint não autenticado.
- **Reentrega de webhook reaplicava o efeito.** Não havia registro de
  `event_id`; handlers capturavam qualquer exceção e devolviam `200`, fazendo o
  provedor considerar a entrega concluída.
- **CI falhava no lint desde 2026-08-08 sem alteração de código.** O conjunto
  default de regras do ruff saltou de 59 para 407 entre versões, com
  `ruff>=0.3` sem limite superior.
- `time.sleep` em função `async` bloqueava o event loop inteiro (`ChaosMiddleware`).
- Caminho previsível em `/tmp` para o heartbeat do worker.

### Added

- Journal append-only por trigger no banco, com deduplicação por fato
  (`UNIQUE (tenant_id, fact_key)`) e reversão vinculada com limite cumulativo.
- Registro de operação no provedor (`gateway_operations`) com identidade
  durável, fingerprint e estado para desfecho incerto.
- Inbox de webhook (`webhook_inbox`) com unicidade `(provider, event_id)`.
- Máquina de estados explícita do pagamento, incluindo `AUTHORIZING`,
  `CAPTURING`, `PARTIALLY_REFUNDED` e `REVIEW`; eventos atrasados não regridem
  a liquidação.
- `resolve_uncertain_operations`: fecha operações incertas por consulta ao
  provedor, com a mesma chave de idempotência.
- `scripts/reindex_blind_index.py` e `docs/operations.md`.
- Testes de integração contra PostgreSQL real: integridade do journal,
  concorrência entre webhook e captura, dois workers, lease expirado, queda
  entre etapas, isolamento de tenant.

### Changed

- Serviços PostgreSQL/Redis/RabbitMQ na CI, com `REQUIRE_INTEGRATION=1` para que
  banco ausente seja falha e não skip.
- `sync-master-with-develop` deixou de promover `develop` para `master` com
  `reset --hard` e force-push; passou a ser verificação somente leitura com link
  para PR manual.
- Publicação de imagem passou a depender da CI do **mesmo** SHA.
- Funções de câmbio deixaram de ser `async` sem motivo (o corpo é síncrono), o
  que eliminou `asyncio.run` espalhado, inclusive um por linha de saldo no
  consolidado.
- Conjunto de regras do ruff declarado explicitamente no `pyproject.toml`;
  `ruff`, `black` e `mypy` com faixa de versão fechada.
- `amount` anotado como `Decimal` nos modelos (era `float` sobre `Numeric`).

## [1.0.0] - 2026-03-07

### Added
- Payment ledger API with full transaction lifecycle
- Stripe integration for real payment processing (async)
- Gateway refund support with proper signature handling
- Webhook delivery system for payment event notifications
- Reconciliation endpoint for payment auditing
- Audit endpoint for payment events and security monitoring
- CI/CD pipeline with GitHub Actions
- Multi-arch Docker build (amd64 + arm64) for Oracle Cloud ARM
- Docker image published to GHCR
- Alembic database migrations
- Test suite with `run-tests` script

### Changed
- README, migrations and requirements updated for production readiness

### Fixed
- Gateway refund signature and webhook delivery model/query corrections
- bcrypt pinned below 4.1 for passlib 1.7 compatibility
- Docker build configuration fixes

### Security
- Security hardening with JWT validation from spring-saas-core
- Stripe webhook signature verification
- Async payment processing to prevent blocking on gateway calls
