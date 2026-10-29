# Operação — py-payments-ledger

Procedimentos que o operador precisa conhecer antes de subir, atualizar ou
recuperar o serviço. O que está aqui são restrições reais do sistema, não
recomendações genéricas.

---

## 1. Cifra em repouso e índice cego

### O que é cifrado

`EncryptedString` (AES-256-GCM) protege, em repouso:

| Tabela | Coluna |
|---|---|
| `payment_intents` | `customer_ref`, `gateway_ref` |
| `refunds` | `gateway_ref` |
| `reconciliation_discrepancies` | `gateway_ref` |
| `webhook_endpoints` | `secret` |
| `gateway_configs` | `api_key_ref` |
| `saved_payment_methods` | `gateway_token` |

A cifra usa **nonce aleatório por escrita**. Isso é a propriedade correta para
confidencialidade e tem uma consequência que não é óbvia:

> O mesmo texto nunca produz o mesmo ciphertext. Portanto
> `WHERE gateway_ref = :valor` **nunca** encontra a linha: o parâmetro é cifrado
> com um nonce novo e comparado contra um ciphertext gravado com outro.

Por isso as buscas por igualdade usam um **índice cego**: um digest
determinístico do plaintext, guardado em coluna própria e indexada.

| Coluna cifrada | Coluna de busca |
|---|---|
| `payment_intents.gateway_ref` | `gateway_ref_bidx` |
| `payment_intents.customer_ref` | `customer_ref_bidx` |
| `saved_payment_methods.gateway_token` | `gateway_token_bidx` |

O digest é `HMAC-SHA256`, com chave derivada de `ENCRYPTION_KEY` por HKDF
(`src/shared/blind_index.py`). Quem ler o banco vê apenas o digest e, sem a
chave, não deriva o plaintext nem confirma um palpite.

A sincronização é automática: um listener `before_flush` recalcula o digest
sempre que o valor cifrado muda (`src/infrastructure/db/models.py`). Nenhum call
site precisa lembrar de atualizar duas colunas.

### Rotação de `ENCRYPTION_KEY` — exige reindexação

**Trocar `ENCRYPTION_KEY` invalida todos os índices cegos.** Os digests antigos
foram derivados da chave antiga e deixam de casar. O efeito é silencioso e
grave: o webhook do provedor não encontra o pagamento, a reconciliação classifica
tudo como `MISSING_LOCAL` e a deduplicação de pedido deixa de reconhecer pedidos
já processados.

Procedimento:

1. **Pare as instâncias** (API e worker). A rotação não é segura em rolling
   update: instâncias com chaves diferentes escreveriam digests incompatíveis.
2. Faça backup (seção 3).
3. Re-cifre os valores com a chave nova. A cifra atual não suporta duas chaves
   simultâneas; o caminho é decifrar com a chave antiga e gravar com a nova —
   fora do escopo deste script, que **não** re-cifra, apenas reindexa.
4. Recalcule os índices cegos:

   ```bash
   DATABASE_URL=... ENCRYPTION_KEY=<chave nova> JWT_SECRET=... \
     python3 scripts/reindex_blind_index.py --apply
   ```

5. Confirme (modo relatório, não escreve):

   ```bash
   DATABASE_URL=... ENCRYPTION_KEY=<chave nova> JWT_SECRET=... \
     python3 scripts/reindex_blind_index.py
   # "todos os digests estao em sincronia"  -> exit 0
   # "N digest(s) fora de sincronia"        -> exit 2
   ```

6. Suba as instâncias.

O script aborta se o ORM devolver ciphertext em vez de plaintext — sinal de que
a `ENCRYPTION_KEY` informada não é a que cifrou os dados. Reindexar nesse estado
gravaria digests do ciphertext, que não casam com nada.

### Primeira definição da chave em base que operava sem cifra

`decrypt()` devolve valores sem o prefixo `enc:v1:` inalterados, portanto dados
legados continuam legíveis. Mas os digests gravados sem chave usam SHA-256
não-chaveado; depois de definir `ENCRYPTION_KEY` é necessário rodar
`reindex_blind_index.py --apply`.

---

## 2. Migrações

```bash
alembic upgrade head
```

### `0023_financial_integrity`

Compatível com base existente. O que ela faz, e o que o operador precisa saber:

- **Calcula os índices cegos** a partir dos valores decifrados pelo ORM. Rode-a
  com a `ENCRYPTION_KEY` correta no ambiente, ou os digests sairão errados.
- **Torna o journal append-only** por trigger (`ledger_entries`,
  `ledger_lines`). Depois desta migração, `UPDATE` e `DELETE` nessas tabelas são
  recusados pelo banco, para qualquer cliente — aplicação, script ou console.
  Correção passa a ser lançamento compensatório.
- **Adiciona `ledger_entries.fact_key`** com unicidade por tenant. Lançamentos
  pré-existentes recebem `legacy:<id>`: um valor único por linha, que **preserva
  eventuais duplicatas já gravadas** em vez de abortar o upgrade. Essas
  duplicatas aparecem no relatório de reconciliação e devem ser tratadas por
  lançamento compensatório, nunca por `DELETE`.
- **Move a unicidade de `saved_payment_methods`** da coluna cifrada
  `gateway_token` para `gateway_token_bidx`. Sobre a coluna cifrada a restrição
  nunca disparava (nonce aleatório), portanto tokens duplicados podem existir na
  base. Se o upgrade falhar ao criar a nova restrição, há duplicatas reais: liste
  e resolva antes de repetir.

  ```sql
  SELECT tenant_id, gateway_provider, gateway_token_bidx, count(*)
  FROM saved_payment_methods
  GROUP BY 1, 2, 3 HAVING count(*) > 1;
  ```

- **Não** exige rolling update seguro: aplique com as instâncias antigas
  paradas. A trigger append-only passa a valer imediatamente, e uma instância
  antiga que tentasse reescrever um lançamento receberia erro.

O `downgrade` existe e remove triggers, colunas e tabelas novas. Ele **não**
restaura a unicidade sobre `gateway_token` de forma efetiva (ela nunca foi
efetiva), e perde `fact_key`, portanto a deduplicação por fato deixa de existir.

---

## 3. Backup e restauração

### Backup

```bash
pg_dump --format=custom --no-owner --no-privileges \
  "$DATABASE_URL" > backup-$(date -u +%Y%m%dT%H%M%SZ).dump
```

Guarde `ENCRYPTION_KEY` **separadamente** do dump. Um dump sem a chave é
ilegível nas colunas cifradas; um dump com a chave ao lado anula a cifra em
repouso.

### Restauração

Restaure em banco **novo e vazio** — nunca reaproveitando o volume, que não
prova restauração funcional.

```bash
createdb restored
pg_restore --dbname=restored --no-owner --no-privileges backup.dump
```

Depois do restore, antes de liberar tráfego:

1. **Mantenha os efeitos externos desligados.** Suba com
   `GATEWAY_PROVIDER=fake` e o worker parado, para que a reconciliação não
   dispare operações contra o provedor com dados restaurados.
2. **Valide pela aplicação, não pelo dump.** `pg_restore` com exit 0 não prova
   restauração funcional:

   ```bash
   # autenticação
   curl -fsS -X POST "$BASE/v1/auth/token" -d '{...}'
   # leitura com permissão
   curl -fsS "$BASE/v1/payment-intents" -H "Authorization: Bearer $T" -H "X-Tenant-Id: $TENANT"
   # invariante contábil por moeda
   psql restored -c "
     SELECT l.currency,
            sum(CASE WHEN l.side='DEBIT' THEN l.amount ELSE 0 END) AS debits,
            sum(CASE WHEN l.side='CREDIT' THEN l.amount ELSE 0 END) AS credits
     FROM ledger_lines l GROUP BY l.currency;"
   ```

   Débitos e créditos devem fechar **por moeda**. Soma zero no total não é
   aprovação contábil.
3. **Confira os índices cegos.** Se o ambiente de destino usa outra
   `ENCRYPTION_KEY`, rode `reindex_blind_index.py --apply` (seção 1).
4. **Verifique a idempotência preservada.** As restrições
   `uq_ledger_entries_tenant_fact`, `uq_gwop_tenant_idem` e
   `uq_webhook_inbox_provider_event` precisam existir no banco restaurado:

   ```sql
   SELECT conname FROM pg_constraint
   WHERE conname IN ('uq_ledger_entries_tenant_fact','uq_gwop_tenant_idem',
                     'uq_webhook_inbox_provider_event');
   ```

   E as triggers append-only:

   ```sql
   SELECT tgname FROM pg_trigger WHERE tgname LIKE '%append_only';
   ```

---

## 4. Fila de revisão: operações de desfecho incerto

Quando uma chamada ao provedor é enviada e a resposta não chega (timeout após o
envio, 5xx, conexão cortada), o desfecho é **desconhecido** — não é recusa. O
serviço registra isso explicitamente em vez de adivinhar:

- a operação fica `UNCERTAIN` em `gateway_operations`;
- o pagamento vai para o estado `REVIEW`;
- **nenhuma operação nova é emitida.** Reenviar poderia duplicar o efeito.

A saída é **consulta**, com a mesma chave de idempotência:

```python
from src.application.reconciliation import resolve_uncertain_operations
resolve_uncertain_operations(session, tenant_id, gateway)
```

Para inspecionar a fila:

```sql
SELECT id, op_type, idempotency_key, status, amount, currency,
       error_code, created_at, updated_at
FROM gateway_operations
WHERE status IN ('UNCERTAIN','PENDING')
ORDER BY created_at;
```

Uma operação `PENDING` com mais de 30 segundos (`IN_FLIGHT_TIMEOUT`) é
considerada órfã: a tentativa anterior morreu sem reportar. A retomada usa a
**mesma** chave — é isso que impede efeito duplicado no provedor, e é diferente
de emitir uma operação nova com chave nova, que é o caminho para a cobrança
dupla.

### Entregas de webhook presas

```sql
SELECT provider, event_id, event_type, status, attempts, error, received_at
FROM webhook_inbox
WHERE status IN ('RECEIVED','FAILED')
ORDER BY received_at;
```

Registro do evento e aplicação do efeito acontecem na **mesma transação**, então
um `RECEIVED` antigo indica um processo que caiu em um ponto improvável. Um
`FAILED` indica que o serviço pediu reentrega ao provedor (resposta `500`).

---

## 5. Instalação limpa e atualização

```bash
# instalação limpa
cp .env.example .env               # hostnames de Docker (postgres/redis/rabbitmq)
docker compose up -d --wait        # readiness por healthcheck, sem sleep
docker compose exec api alembic upgrade head
./scripts/smoke.sh

# atualização
docker compose down                 # instâncias antigas param
docker compose pull
docker compose up -d --wait
docker compose exec api alembic upgrade head
```

> `.env.example` é o arquivo para rodar **dentro** do Docker. `.env.local.example`
> existe para o caso de rodar a API na máquina com a infraestrutura do
> `spring-saas-core` (portas 5435/6382/5675) — ver `docs/CONFIG-AMBIENTES.md`.
> Usar o segundo com `docker compose up` faz o container tentar `localhost` e falhar.

`--wait` espera os healthchecks, não um tempo fixo. Note que um processo `UP`
pode não ter capacidade de cobrar: `/readyz` verifica banco, Redis e (quando
`READINESS_REQUIRE_RABBIT=1`) o broker.

`/readyz` responde `503` com apenas `{"status":"fail","component":"<nome>"}`. A
mensagem do driver fica **somente** no log do servidor: ela carrega host, porta,
usuário e base, e o endpoint não é autenticado.

---

## 6. Variáveis que não têm default compartilhado

| Variável | Obrigatória em | Observação |
|---|---|---|
| `JWT_SECRET` ou `JWKS_URI`/`JWT_PUBLIC_KEY` | todos | sem ela a configuração não carrega |
| `ENCRYPTION_KEY` | produção, staging | 32 bytes em base64; rotação exige reindexação (seção 1) |
| `STRIPE_API_KEY` | `GATEWAY_PROVIDER=stripe` | a configuração falha sem ela |
| `STRIPE_WEBHOOK_SECRET` | webhook Stripe | sem ela nenhuma assinatura valida |
| `DATABASE_URL`, `REDIS_URL`, `RABBITMQ_URL` | todos | — |
| `WORKER_HEARTBEAT_PATH` | opcional | default no diretório temporário do processo |
| `CROSS_TENANT_REQUIRE_PERMISSION` | opcional | `false` por default. Ligar exige a permissão `tenant:any` nos tokens com `tid="*"`; **só ligue depois** de o emissor passar a incluí-la, ou os tokens administrativos em uso serão recusados. Ver `docs/contracts/identity.md`. |

`GATEWAY_PROVIDER=fake` é destinado a desenvolvimento e demonstração. Em
produção, configure um provedor real: o adaptador fake não move dinheiro.

---

## 7. Vulnerabilidades da imagem — gate bloqueado pela base

Medição de **2026-10-29**, trivy (base de dados do dia), `CRITICAL,HIGH`,
`--scanners vuln`, sem `ignore-unfixed`:

| Alvo | CRITICAL | HIGH | Com correção disponível |
|---|---:|---:|---:|
| `pplx-api` (imagem final, Debian 13.7) | 0 | **44** | **0** |
| `python:3.12-slim` (base, Debian 13.7) | 0 | 44 | 0 |
| `python:3.12-slim-bookworm` (alternativa, Debian 12.15) | 2 | 59 | 0 |

**Os 44 achados não têm correção.** Todos estão em pacotes do sistema da
imagem base, com status `affected` (43) ou `fix_deferred` (1) no Debian:
`util-linux` e satélites (`bsdutils`, `libblkid1`, `libmount1`, `libsmartcols1`,
`libuuid1`, `liblastlog2-2`, `login`, `mount`), `ncurses`, `libsystemd0`,
`libudev1`, `libacl1`, `perl-base`.

O que foi feito, e o que não foi:

- **Base escolhida com medição, não por hábito.** Debian 13 tem menos achados e
  nenhum CRITICAL; migrar para bookworm **pioraria** o resultado. Registrado no
  comentário do `Dockerfile`.
- **Base fixada por digest de lista de manifestos**
  (`sha256:aa943ce4...`), cobrindo `linux/amd64` e `linux/arm64` — as duas
  plataformas publicadas. Fixar digest torna o artefato reproduzível; **não
  corrige CVE**.
- **`curl` removido do runtime.** Existia apenas para o `HEALTHCHECK` e
  respondia por 4 achados (`curl` + `libcurl4t64`); o healthcheck passou a usar
  o Python que já está na imagem. Reduziu de 48 para 44 — verificado por
  re-scan.
- **Severidade não foi reduzida e nenhum achado foi silenciado.** O scan de
  dependências continua sendo gate **bloqueante** (job `security` do workflow
  `ci`, trivy em modo filesystem, `exit-code: 1`). O scan da **imagem** foi
  adicionado ao `build-push` como **informativo**, com o relatório publicado
  como artefato e no resumo da execução.

**Como destravar.** Quando o Debian publicar correção para `util-linux` e
`ncurses`, atualize o digest da base
(`docker buildx imagetools inspect python:3.12-slim`), reconstrua, re-escaneie e
troque `exit-code: '0'` para `'1'` na etapa de scan da imagem em
`.github/workflows/build-push.yml`. Até então, o gate de imagem permanece
bloqueado por dependência externa — não por decisão de configuração.
