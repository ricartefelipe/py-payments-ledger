# py-payments-ledger

Projeto demonstrativo (FastAPI + Worker): pagamentos + razão contábil (double-entry ledger) com padrões de produção como outbox, idempotência, RBAC/ABAC, limitação por Redis e observabilidade (Prometheus/Grafana).

## O que este repositório oferece
- Padrão Outbox: a API grava estado + eventos outbox na mesma transação do banco; um worker consome e publica para RabbitMQ.
- Garantia de publicação ao menos-uma-vez (RabbitMQ) com fila de DLQ.
- Confirmação idempotente via cabeçalho `Idempotency-Key` (Redis).
- Suporte multi-tenant via cabeçalho `X-Tenant-Id`.
- Autenticação/Autorização: JWT (demo HS256) com RBAC e ABAC (policy por plan/region).
- Observabilidade: métricas em `/metrics` (Prometheus) e dashboards do Grafana já provisionados.

## Quickstart (10 minutos)
Executar localmente com os scripts fornecidos (Linux/macOS/WSL ou PowerShell):

No Windows PowerShell (exemplo):

1. Copie o .env de exemplo:
   cp .env.example .env

2. Suba os serviços (Docker Compose) e crie o ambiente:
   .\scripts\up.sh

3. Rode migrações e seed de dados:
   .\scripts\migrate.sh
   .\scripts\seed.sh

4. Teste a aplicação rapidamente:
   .\scripts\smoke.sh

URLs úteis (padrão local):
- API docs (Swagger): http://localhost:8000/docs
- RabbitMQ UI: http://localhost:15672 (guest/guest)
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (admin/admin)

## Credenciais de demonstração
O seed cria usuários de exemplo:
- Admin global: `admin@local` / `admin123` (tid="*")
- Ops: `ops@demo` / `ops123` (tenant_demo)
- Sales: `sales@demo` / `sales123` (tenant_demo)

## Documentação da API
- Swagger UI: `/docs`
- OpenAPI JSON: `/openapi.json`
- Arquivos da especificação: `docs/api/openapi.json`, `docs/api/openapi.yaml`
- Exportar/atualizar spec: `./scripts/api-export.sh`

## Execução com Docker Compose
O repositório inclui `docker-compose.yml` e Dockerfiles para a API e worker. Para subir tudo com Docker Compose:

1. Certifique-se de que o Docker e o Docker Compose estão instalados.
2. Copie o `.env.example` para `.env` e ajuste variáveis se preciso.
3. Rode:

```powershell
# no PowerShell
.\scripts\up.sh
```

Os scripts usam `docker-compose` internamente. Para encerrar o ambiente:

```powershell
.\scripts\down.sh
```

## Execução local sem Docker
Se preferir rodar apenas a API localmente (Python v3.12+):

1. Crie e ative um virtualenv (PowerShell):

```powershell
python -m venv .venv
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process -Force; .\.venv\Scripts\Activate.ps1
```

2. Atualize pip e instale o pacote e dependências de desenvolvimento:

```powershell
python -m pip install --upgrade pip
pip install -e .
pip install -r requirements-dev.txt
```

3. Rode a aplicação (exemplo com uvicorn):

```powershell
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
```

## Testes
A suíte de testes usa `pytest` e `pytest-asyncio`. Para rodar os testes localmente (PowerShell):

```powershell
# ative .venv primeiro
python -m pytest -q
```

Observação: durante os testes de exemplo pode aparecer um aviso sobre comprimento de chave HMAC (chave curta usada apenas para demo). Em produção, use um `JWT_SECRET` com comprimento seguro (recomendado >=32 bytes).

## Dica de segurança
- Nunca use `jwt_secret` fraca em produção — utilize segredos gerenciados (HashiCorp Vault, AWS Secrets Manager, etc.) e chaves de tamanho adequado.
- Não exponha credenciais em repositórios públicos.
- Configure TLS para todas as comunicações entre serviços.

## Estrutura principal de pastas
- `src/api`: routers e middlewares (FastAPI)
- `src/worker`: dispatcher do outbox e consumidores
- `src/application`: regras de negócios e orquestração
- `src/infrastructure`: integração com DB, Redis, RabbitMQ
- `migrations`: migrações Alembic
- `docs`: documentação e especificações OpenAPI
- `observability`: dashboards e configurações do Prometheus/Grafana
- `scripts`: scripts utilitários (start, migrate, seed, smoke)

## Contribuição
Contribuições são bem-vindas. Por favor abra issues ou PRs com descrições claras das mudanças.

Recomendações para contribuições:
- Abra uma branch específica: `git checkout -b feat/minha-mudanca`.
- Execute os testes localmente antes de enviar o PR.
- Siga o estilo do projeto (`ruff`, `black`) e as regras de tipagem (`mypy`).

## Licença
MIT
