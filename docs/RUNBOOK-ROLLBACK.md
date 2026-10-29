# Runbook de rollback - py-payments-ledger

## Gatilhos

- erro 5xx acima do baseline por 10 minutos
- falha persistente em authorize/capture/settle
- erro de consumo/publicacao de eventos de pagamento

## Procedimento

1. Congelar novas promocoes
2. Identificar ultima imagem/tag estavel de API e worker
3. Reverter API e worker para a mesma versao anterior
4. Validar `/healthz`
5. Executar fluxo minimo de payment intent (create -> confirm -> settled)
6. Confirmar emissao de evento `payment.settled`

## Restricoes de rollback introduzidas pela migracao 0023

- **O journal e append-only por trigger.** `UPDATE` e `DELETE` em
  `ledger_entries` e `ledger_lines` sao recusados pelo banco. Nao existe
  "corrigir o saldo" durante um incidente: a correcao e lancamento
  compensatorio (`reverse_entry`).
- **Nao reverta a migracao para destravar uma operacao.** `alembic downgrade`
  remove `fact_key` e com ele a deduplicacao por fato: a versao anterior do
  codigo poderia lancar duas vezes o mesmo fato. Reverta apenas a imagem.
- **Imagem antiga + schema novo e suportado.** `fact_key` tem default no banco
  (`'legacy:' || gen_random_uuid()`), portanto a versao anterior do codigo, que
  insere sem essa coluna, continua lancando. Esses lancamentos **nao**
  participam da deduplicacao por fato — e justamente a garantia que a versao
  antiga nao oferecia. Rollback de imagem nao exige downgrade de schema.
- **Operacoes em `UNCERTAIN` nao devem ser reemitidas durante o rollback.**
  Consulte a fila de revisao (`docs/operations.md`, secao 4) antes de reprocessar
  qualquer pagamento.

## Validacao pos-rollback

- `./scripts/smoke.sh` no ambiente alvo
- ledger consistente e sem backlog critico em fila
- incidente atualizado com causa raiz e acao preventiva
