# Oferta — py-payments-ledger

Documento para avaliação comercial. Descreve o que o serviço entrega hoje, o
que **não** entrega, e o que depende de decisão ou contratação do proprietário.

> Este serviço é um **motor de integração de pagamentos com razão contábil
> auditável**. Não é banco, não é adquirente e não é sistema de contabilidade
> homologado. Não há parecer contábil ou jurídico associado a esta entrega.

---

## 1. Comprador e problema

**Quem compra.** Equipe de engenharia de um produto B2B que já vende e já tem um
provedor de pagamento, e precisa de uma camada própria entre o produto e o
provedor.

**O problema.** Quem integra um gateway diretamente no serviço de pedidos acaba
com três dívidas que só aparecem em produção:

1. **Cobrança duplicada.** O provedor aceita a autorização, a resposta não
   chega, o serviço trata como falha e tenta de novo com uma chave nova.
2. **Estado financeiro sem fato contábil.** O webhook do provedor marca o
   pagamento como pago, mas nada é lançado no razão — e o número do relatório
   deixa de ter origem rastreável.
3. **Conciliação manual.** Sem registro de qual operação foi emitida, com qual
   identidade e com qual desfecho, a divergência entre o provedor e o sistema
   só é descoberta no fechamento.

**O que este serviço resolve.** Uma identidade durável por obrigação, um razão
append-only em que cada lançamento tem origem declarada, e um caminho único de
finalização pelo qual comando HTTP, consumer, webhook e reconciliação
convergem — de modo que não existe forma de mudar o estado de um pagamento sem
gravar o lançamento e o evento correspondentes.

---

## 2. Funções incluídas

| Área | Inclui |
|---|---|
| Pagamentos | payment intents (create/confirm/capture/void), estorno total e parcial, pagamento com método salvo |
| Razão | partidas dobradas, append-only por trigger, equilíbrio por moeda, reversão vinculada ao lançamento original, extrato e saldos por conta |
| Idempotência | chave por obrigação com fingerprint; registro do desfecho de cada operação remota, incluindo o caso "desfecho desconhecido" |
| Provedores | Stripe, PagSeguro, Mercado Pago e um adaptador fake para desenvolvimento; roteamento por moeda e tipo de pagamento |
| Webhooks | entrada dos três provedores com verificação de assinatura e deduplicação por evento; entrega de saída para integradores, com retentativa |
| Integração | consome `payment.charge_requested` / `order.confirmed` e publica `payment.authorized` / `payment.settled` via outbox e RabbitMQ |
| Reconciliação | comparação provedor × estado local × razão, com registro de divergências e resolução de operações de desfecho incerto por consulta |
| Multi-tenant | isolamento por tenant em todas as consultas; RBAC/ABAC sobre JWT emitido por outro serviço |
| Operação | migrações versionadas, backup/restauração documentados, trilha de auditoria consultável, métricas Prometheus, tracing OpenTelemetry |
| Segurança | cifra em repouso (AES-256-GCM) nas colunas sensíveis, com índice cego para busca; rate limiting distribuído; circuit breaker por provedor |

---

## 3. Funções excluídas — por desenho

| Não inclui | Razão |
|---|---|
| Adquirência, custódia ou conta de pagamento | exige autorização regulatória; fora do escopo do produto |
| Armazenamento de PAN/CVV | a tokenização é delegada ao provedor; guardar esses dados traria obrigação de PCI-DSS sem benefício |
| Contabilidade homologada / emissão fiscal | o razão é auditável, mas o plano de contas de exemplo precisa de validação por especialista contábil antes de uso fiscal |
| Interface de backoffice | o serviço é API; não há tela. Há demanda registrada como P2 |
| Antifraude com modelo próprio | há métricas e sinais; decisão de risco é do comprador |
| Parecer de conformidade (PCI, LGPD, SOC) | `docs/compliance.md` descreve os controles implementados; não constitui certificação |

---

## 4. Modalidade e licença — a decidir pelo proprietário

O repositório está hoje sob **MIT** (`LICENSE`). As modalidades abaixo são
alternativas **não decididas**; nenhuma foi contratada, precificada ou
acordada, e nenhum preço é sugerido aqui.

| Modalidade | O que implica |
|---|---|
| Código aberto como está (MIT) | adoção livre; receita viria de serviço, não de licença |
| Licença comercial por instalação | exige troca de licença e termos próprios |
| Serviço gerenciado | exige SLA, plantão, infraestrutura e responsabilidade sobre dados de terceiros |

A escolha é financeira e jurídica e permanece com o proprietário. Trocar a
licença de um repositório com histórico público tem efeitos que merecem
avaliação jurídica.

---

## 5. Custos e dependências reais

O serviço não tem custo de licença próprio. Para operar, dependem de contratação
ou provisionamento:

| Dependência | Natureza | Situação |
|---|---|---|
| Conta no provedor de pagamento (Stripe / PagSeguro / Mercado Pago) | contratação do comprador | **não contratada** |
| Homologação com o provedor | processo do provedor | **não realizada** — sem credencial não há como comprovar |
| PostgreSQL 16, Redis 7, RabbitMQ 3 | infraestrutura | compose local incluído; produção é do comprador |
| Emissor de JWT (`spring-saas-core` ou equivalente) | integração | contrato documentado em `docs/contracts/identity.md` |
| Registry de imagens | infraestrutura | GHCR configurado no workflow |
| Observabilidade (Prometheus/Grafana/Jaeger) | opcional | compose local incluído |

**O que não pode ser afirmado hoje:** desempenho sob carga real, taxa de sucesso
com provedor em produção e tempo de conciliação em volume. Nenhuma dessas
medidas foi feita com tráfego real.

---

## 6. Onboarding e offboarding

**Onboarding (ambiente do comprador).**

1. `docker compose up -d --wait` e `alembic upgrade head`.
2. Definir `JWT_SECRET`/`JWKS_URI`, `ENCRYPTION_KEY` e as credenciais do
   provedor (ver `docs/operations.md`, seção 6).
3. Cadastrar o tenant e o plano de contas; o seed traz um exemplo.
4. Rodar `./scripts/smoke.sh` e, com o provedor em sandbox, um pagamento de
   ponta a ponta.
5. Apontar os webhooks do provedor para `/webhooks/<provedor>`.

Sem credencial de provedor, o onboarding chega até o passo 4 com o adaptador
fake — o que exercita o fluxo completo, mas **não** movimenta dinheiro.

**Offboarding.** Os dados são do comprador e ficam no PostgreSQL dele.
`pg_dump` produz o conjunto completo; `GET /v1/reports/*` exporta razão e
auditoria em CSV/JSON. A cifra em repouso exige levar a `ENCRYPTION_KEY`: sem
ela as colunas sensíveis ficam ilegíveis. Não há retenção de dados do lado do
fornecedor, porque não há lado do fornecedor nesta modalidade.

---

## 7. Demonstração

A demonstração usa **dados sintéticos** e o adaptador **fake** de gateway.
Nenhum pagamento real é processado e nenhuma mensagem é enviada a terceiros.

```bash
./scripts/portfolio-up.sh      # sobe tudo e popula dados de demonstração
./scripts/seed.sh              # massa sintética
```

O que a demonstração mostra: ciclo completo do pagamento, razão com partidas
dobradas, reconciliação detectando uma divergência plantada, trilha de auditoria
e as métricas. O que ela **não** mostra: comportamento com provedor real,
latência de produção e volume.

---

## 8. Suporte e condições de piloto

Não há contrato de suporte, SLA ou plantão em vigor. Um piloto exigiria acordo
prévio sobre:

- janela do piloto e volume máximo de transações;
- quem responde por uma falha que gere divergência financeira;
- política de retenção e acesso aos dados durante e depois do piloto;
- ambiente dedicado (o piloto não deve compartilhar banco com outro produto);
- credencial de sandbox do provedor, fornecida pelo comprador.

Enquanto esses pontos não forem acordados por escrito, o uso recomendado é
avaliação técnica em ambiente isolado.

---

## 9. Itens avaliados e adiados

Registrados em `ROADMAP.md` com benefício, esforço e motivo. Resumo:

| Item | Benefício | Esforço | Por que adiar |
|---|---|---|---|
| Reprocessamento automático da fila de revisão | reduz intervenção manual | médio | depende da política de retenção de chave de cada provedor, por endpoint |
| Backoffice com aprovação de operações sensíveis | controle operacional | alto | sem segundo caso de uso que justifique a interface |
| Exportação contábil em formato de terceiros | integra com contabilidade | médio | exige validação por especialista antes de prometer formato |
| Conectores de outros provedores | alcance comercial | médio por provedor | só com demanda concreta e conta de sandbox |
| Multi-região, sharding, Kubernetes obrigatório | escala | alto | nenhum limite concreto foi demonstrado que justifique |
| Event sourcing completo | auditabilidade | alto | o razão append-only já entrega a trilha necessária |

Internacionalização, SSO, SDKs, busca avançada, cache adicional e recuperação de
desastre multi-região foram avaliados e classificados como P2/P3: nenhum é
exigido pela oferta descrita acima.
