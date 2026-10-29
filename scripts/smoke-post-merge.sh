#!/usr/bin/env bash
set -euo pipefail

# Smoke HTTP pos-deploy (staging). Health + OpenAPI; opcionalmente readiness
# (DB + Redis e, se o servidor tiver READINESS_REQUIRE_RABBIT=true, ligacao ao
# RabbitMQ).
#
# Variaveis:
#   PAYMENTS_SMOKE_URL ou SMOKE_BASE_URL — endereco do staging
#   SMOKE_CHECK_READYZ=1  — chama GET /readyz
#   SMOKE_REQUIRE_URL=1   — exige que a URL esteja definida
#   SMOKE_REQUIRE_LIVE=1  — exige que o staging responda (ver abaixo)
#
# Sobre SMOKE_REQUIRE_LIVE
# ------------------------
# Por padrao, um staging que nao responde **nao** derruba a pipeline: a
# conferencia e reportada como ignorada, com instrucoes de como religar.
#
# A razao e pratica. Entre 20/06/2026 e 29/10/2026 esta conferencia falhou em
# cinco merges seguidos porque o ambiente de staging deixou de existir — o
# endereco respondia 404, que no Railway e a resposta para um host que nao
# aponta para nenhum servico. Um check vermelho que ninguem pode resolver vira
# ruido, e ruido treina a equipe a ignorar a pipeline inclusive quando ela
# avisa algo real.
#
# Isto **nao** e `continue-on-error`: a diferenca entre "staging ausente" e
# "staging quebrado" e preservada. Com `SMOKE_REQUIRE_LIVE=1` a conferencia
# volta a ser um gate que falha, e ai um staging fora do ar quebra a pipeline
# como deve.

BASE_URL="${PAYMENTS_SMOKE_URL:-${SMOKE_BASE_URL:-}}"
REQUIRE_URL="${SMOKE_REQUIRE_URL:-0}"
REQUIRE_LIVE="${SMOKE_REQUIRE_LIVE:-0}"

# Resumo da execucao no GitHub Actions; em execucao local, descartado.
summary() {
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    echo "$1" >> "$GITHUB_STEP_SUMMARY"
  fi
}

reconfig_instructions() {
  cat <<'TXT'

Como religar esta conferencia
-----------------------------
1. No Railway, confirme o endereco publico do servico de staging do
   py-payments-ledger. Se o servico nao existir mais, recrie-o (o deploy usa
   docker/api.Dockerfile e healthcheck em /healthz — ver railway.toml).

2. No GitHub, atualize o segredo com esse endereco:
   Settings -> Secrets and variables -> Actions -> PAYMENTS_SMOKE_URL

3. Para que a conferencia volte a ser obrigatoria, crie a variavel:
   Settings -> Secrets and variables -> Actions -> Variables ->
   SMOKE_REQUIRE_LIVE = 1

   Sem o passo 3 a conferencia continua informativa: ela avisa, mas nao
   derruba a pipeline.
TXT
}

if [[ -z "$BASE_URL" ]]; then
  echo "[smoke] PAYMENTS_SMOKE_URL/SMOKE_BASE_URL nao definido — conferencia ignorada."
  summary "## Smoke de staging: ignorado"
  summary ""
  summary "Nenhum endereco de staging configurado (\`PAYMENTS_SMOKE_URL\`)."
  summary "Ver \`docs/operations.md\`, secao 8."
  if [[ "$REQUIRE_URL" == "1" ]]; then
    echo "[smoke] SMOKE_REQUIRE_URL=1 — endereco e obrigatorio."
    exit 1
  fi
  exit 0
fi

BASE_URL="${BASE_URL%/}"
echo "[smoke] py-payments-ledger — base ${BASE_URL}"

# Sondagem: o endereco aponta para uma aplicacao viva?
#
# Distinguir os casos importa. 404 e a resposta do Railway para um host sem
# servico associado — ou seja, "staging nao existe". Um app rodando responde
# 200 em /healthz; um app quebrado responde 5xx. So o primeiro caso e tratado
# como ambiente ausente.
PROBE_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "$BASE_URL/healthz" 2>/dev/null || echo "000")

case "$PROBE_CODE" in
  000|404)
    if [[ "$PROBE_CODE" == "000" ]]; then
      REASON="o endereco nao respondeu (DNS, conexao ou tempo esgotado)"
    else
      REASON="o endereco respondeu 404 — nao aponta para uma aplicacao"
    fi

    echo "[smoke] staging indisponivel: ${REASON}"
    summary "## Smoke de staging: ignorado"
    summary ""
    summary "O endereco configurado em \`PAYMENTS_SMOKE_URL\` esta indisponivel:"
    summary "**${REASON}**."
    summary ""
    summary "A conferencia foi ignorada em vez de derrubar a pipeline, porque um"
    summary "check vermelho que ninguem pode resolver treina a equipe a ignorar o CI."
    summary ""
    summary "Para religar, ver \`docs/operations.md\`, secao 8 — sao dois passos no"
    summary "Railway e no GitHub, sem mexer em codigo."

    if [[ "$REQUIRE_LIVE" == "1" ]]; then
      echo "[smoke] SMOKE_REQUIRE_LIVE=1 — staging e obrigatorio."
      reconfig_instructions
      exit 1
    fi

    reconfig_instructions
    exit 0
    ;;
esac

# Daqui para baixo o staging respondeu: falha e falha de verdade.
curl -sfS --max-time 20 "$BASE_URL/healthz" >/dev/null || {
  echo "[smoke] FALHA: GET /healthz (HTTP ${PROBE_CODE})"
  summary "## Smoke de staging: FALHOU"
  summary ""
  summary "O staging respondeu, mas \`/healthz\` retornou HTTP \`${PROBE_CODE}\`."
  summary "A aplicacao esta no ar e com problema — nao e ambiente ausente."
  exit 1
}
echo "[smoke] OK /healthz"

BODY=$(curl -sfS --max-time 20 "$BASE_URL/openapi.json") || {
  echo "[smoke] FALHA: GET /openapi.json"
  exit 1
}
if ! grep -q openapi <<<"$BODY"; then
  echo "[smoke] FALHA: openapi.json invalido"
  exit 1
fi
echo "[smoke] OK /openapi.json"

if [[ "${SMOKE_CHECK_READYZ:-0}" == "1" ]]; then
  curl -sfS --max-time 25 "$BASE_URL/readyz" >/dev/null || {
    echo "[smoke] FALHA: GET /readyz (DB/Redis/Rabbit pelo servidor)"
    exit 1
  }
  echo "[smoke] OK /readyz"
fi

echo "[smoke] concluido com sucesso"
summary "## Smoke de staging: OK"
summary ""
summary "\`/healthz\`, \`/openapi.json\`${SMOKE_CHECK_READYZ:+ e \`/readyz\`} responderam."
