# AGENTS.md — Diretrizes para assistentes no py-payments-ledger

Regras para quem altera este repositório (humanos ou assistentes automatizados).

---

## Git Flow

- Branches: `master` (produção), `develop` (staging), `feature/*`, `fix/*`, `docs/*`.
- Trabalho novo: criar `feature/...` ou `fix/...` a partir de `develop` atualizada; **nunca** commit direto em `develop` ou `master`.
- Integração em `develop`: via PR ou merge local equivalente; **CI verde** antes de mergear.
- **Release** `develop` → `master`: só quando o responsável pedir.

---

## Qualidade e verificação

- Python ≥ 3.12; `ruff check`, `black` (se configurado no CI), `pytest`.
- Ambiente: preferir virtualenv (`.venv`); ver `pyproject.toml`.
- Contratos com spring-saas-core / node-b2b-orders: ver `docs/PROMPT-EVOLUCAO.md`, `docs/compliance.md`.

---

## Commits e documentação

- Mensagens de commit **claras**.
- **Não** incluir marcas comerciais de IDEs ou assistentes em commits, PRs ou documentação (nem rodapés automáticos).

---

## Papel do agente (delegação)

- Pode executar no repo: branches, código, testes, formatação, commit, push, PR e merge em `develop` após CI verde.
- **Limites:** sem painéis cloud nem credenciais; sem `sudo` na máquina do utilizador.

---

## Referências

- `docs/PROMPT-EVOLUCAO.md`
- Git Flow / pipeline (canónico): [PIPELINE-ESTEIRAS.md](https://github.com/ricartefelipe/fluxe-b2b-suite/blob/develop/docs/PIPELINE-ESTEIRAS.md) no **fluxe-b2b-suite**
