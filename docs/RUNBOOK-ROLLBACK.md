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

## Validacao pos-rollback

- `./scripts/smoke.sh` no ambiente alvo
- ledger consistente e sem backlog critico em fila
- incidente atualizado com causa raiz e acao preventiva
