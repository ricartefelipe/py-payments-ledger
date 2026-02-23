# Contratos de Eventos — py-payments-ledger × node-b2b-orders

Formato canônico: **snake_case**. O consumer aceita camelCase e snake_case.

---

## Eventos consumidos por py-payments-ledger

### 1. `payment.charge_requested` (canônico)

Publicado pelo worker do **node-b2b-orders** quando um pedido deve ser cobrado.

```json
{
  "order_id": "550e8400-e29b-41d4-a716-446655440000",
  "tenant_id": "tenant_demo",
  "total_amount": 150.00,
  "currency": "BRL",
  "customer_ref": "CUST-001",
  "correlation_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

**Alternativa (camelCase):**

```json
{
  "orderId": "550e8400-e29b-41d4-a716-446655440000",
  "tenantId": "tenant_demo",
  "totalAmount": 150.00,
  "currency": "BRL",
  "customerRef": "CUST-001",
  "correlationId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

| Campo          | Obrigatório | Descrição                          |
|----------------|-------------|------------------------------------|
| order_id       | Sim         | ID do pedido                       |
| tenant_id      | Sim         | ID do tenant                       |
| total_amount   | Sim         | Valor a cobrar                     |
| currency       | Não         | Default: BRL                       |
| customer_ref   | Não         | Referência externa                 |
| correlation_id | Não        | Rastreamento (default gerado)      |

---

### 2. `order.confirmed` (legado)

Mantido para compatibilidade. Mesma semântica de `payment.charge_requested`.

```json
{
  "order_id": "550e8400-e29b-41d4-a716-446655440000",
  "tenant_id": "tenant_demo",
  "total_amount": 99.90,
  "currency": "BRL",
  "correlation_id": "corr-xyz"
}
```

---

## Eventos produzidos por py-payments-ledger

### 1. `payment.settled`

Emitido após o ledger ser lançado. Consumido pelo **node-b2b-orders** para marcar pedido como PAID.

```json
{
  "order_id": "550e8400-e29b-41d4-a716-446655440000",
  "tenant_id": "tenant_demo",
  "correlation_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "payment_intent_id": "660e8400-e29b-41d4-a716-446655440001",
  "status": "SETTLED",
  "amount": "150.00",
  "currency": "BRL"
}
```

| Campo             | Descrição                            |
|-------------------|--------------------------------------|
| order_id          | ID do pedido (vazio se não vier de order) |
| tenant_id         | ID do tenant                         |
| correlation_id   | Correlação da requisição original   |
| payment_intent_id | ID do PaymentIntent                  |
| status            | Sempre "SETTLED"                     |
| amount            | Valor como string decimal            |
| currency          | Moeda                                |

**Compatibilidade:** Para consumers que preferem camelCase, use `orderId`, `tenantId`, `correlationId` como aliases (adicionar se necessário).

---

## Fluxo de integração

```
node-b2b-orders                    py-payments-ledger
       |                                    |
       |  payment.charge_requested           |
       |  (ou order.confirmed)               |
       |----------------------------------->|
       |                                    |  Cria PaymentIntent AUTHORIZED
       |                                    |  Outbox: payment.authorized
       |                                    |
       |                                    |  Worker consome payment.authorized
       |                                    |  Posta ledger, status SETTLED
       |  payment.settled                   |
       |<-----------------------------------|
       |  Marca pedido PAID                 |
```

---

## Binding RabbitMQ

| Exchange | Queue                   | Routing keys                                    |
|----------|-------------------------|-------------------------------------------------|
| orders.x | payments.orders.events | payment.charge_requested, order.confirmed        |

Variável de ambiente: `ORDERS_ROUTING_KEYS=payment.charge_requested,order.confirmed`
