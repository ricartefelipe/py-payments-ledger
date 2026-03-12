# Privacy Policy — py-payments-ledger

Data privacy and handling policy for the Payments Ledger service. Includes PCI-DSS awareness and payment data handling.

Last updated: 2026-03-12

---

## 1. PCI-DSS awareness for payment data

py-payments-ledger is designed to minimize PCI-DSS scope:

- **No cardholder data stored** — Card numbers, CVVs, expiry dates, and full magnetic stripe data are never stored. Card collection and tokenization are delegated exclusively to the payment gateway (Stripe).
- **Gateway delegation** — Payment authorizations and captures are performed via the gateway API. The service stores only opaque references (`gateway_ref`), amounts, currencies, and status.
- **Reduced compliance scope** — By not storing, processing, or transmitting cardholder data, the service operates in a reduced PCI-DSS scope. Full PCI-DSS compliance for the gateway is the responsibility of Stripe (or the configured provider).

---

## 2. What payment data is stored vs delegated to gateway

| Stored locally | Delegated to gateway |
|----------------|----------------------|
| Payment intent ID, tenant ID | Card number, CVV, expiry |
| Amount, currency, status | Tokenization, authorization |
| `gateway_ref` (opaque reference, e.g. Stripe PaymentIntent ID) | Capture, refund, void |
| `customer_ref` (tenant-defined customer identifier) | 3D Secure, fraud checks |
| Ledger entries (debit/credit, account codes) | Settlement details |

**Important:** The gateway (Stripe) holds all sensitive payment instrument data. py-payments-ledger never receives or persists PAN (Primary Account Number) or other cardholder data.

---

## 3. Gateway credentials handling

- **Environment variables only** — `STRIPE_API_KEY` and `STRIPE_WEBHOOK_SECRET` are loaded from environment variables. Credentials are never hardcoded or committed to version control.
- **Validation** — When `GATEWAY_PROVIDER=stripe`, the service validates that `STRIPE_API_KEY` is set before startup.
- **Runtime usage** — API keys are passed to the Stripe SDK for HTTP requests; they are not logged, written to audit logs, or exposed in API responses.
- **Secrets management** — Operators should use a secrets manager (e.g., HashiCorp Vault, cloud provider secrets) and inject credentials at deployment time.

---

## 4. PII in audit logs

| Field | Content | PII consideration |
|-------|---------|--------------------|
| `actor_sub` | User identifier from JWT (often email) | Yes — may identify the acting user |
| `tenant_id` | Tenant identifier | No |
| `action`, `target`, `detail` | Action type, resource ID, metadata | `detail` contains operational data (amounts, status); avoid adding PII |

- **Minimization** — `actor_sub` is necessary for accountability. Consider anonymizing or truncating in exports for long-term retention if policy requires.
- **No payment data in audit** — Audit logs do not store card numbers, gateway tokens, or other payment credentials.
- **Export** — `GET /v1/audit/export` returns audit data; access requires `audit:read` permission and is tenant-scoped.

---

## 5. Data retention policies

| Data type | Retention | Configuration |
|-----------|-----------|---------------|
| Audit logs | Configurable | `AUDIT_RETENTION_DAYS` (default: 90 days) |
| Payment intents | Retained while tenant is active | Purged or archived on tenant deletion |
| Ledger entries | Indefinite for financial records | Subject to regulatory requirements |
| Invoices | Retained while tenant is active | May contain `buyer_name`, `buyer_email`, `buyer_tax_id` |

Records beyond the configured retention period may be archived or permanently removed according to the operator's policy. See [`docs/compliance.md`](compliance.md) for audit retention details.

---

## 6. Data access controls (ABAC, audit logging)

- **ABAC** — Attribute-Based Access Control with DENY precedence and default-deny. Policies evaluate `plan`, `region`, and other JWT claims.
- **RBAC** — Permissions in the JWT (`perms`) control access to payments, refunds, audit, invoices, etc.
- **Tenant isolation** — All queries are scoped by `tenant_id`; cross-tenant access is denied.
- **Audit logging** — All sensitive actions (payment intents, refunds, ledger adjustments, webhooks, reconciliations) and access denials (`authz.denied`) are logged with actor, action, target, and correlation ID.

---

## 7. Right to erasure considerations

- **Payment intents** — Store `customer_ref` only; no PII beyond tenant-defined identifiers. Erasure of payment history may conflict with financial/legal retention requirements — consult legal/compliance.
- **Invoices** — Contain `buyer_name`, `buyer_email`, `buyer_tax_id`. Right-to-erasure requests for invoice data should be handled by the platform operator; consider anonymization (e.g., redact buyer fields) where permissible.
- **Audit logs** — `actor_sub` may be PII. Anonymize or redact in response to erasure requests within the retention window. Historical records beyond retention can be purged.
- **Cross-service** — Events consumed from node-b2b-orders and spring-saas-core are processed once; erasure in py-payments-ledger does not affect downstream. Coordinate with the platform operator for full erasure across services.

---

## 8. Encryption at rest and in transit

| Layer | Protection |
|-------|-------------|
| **Transport (TLS)** | All API traffic must use HTTPS. TLS 1.2+ recommended. |
| **Database** | PostgreSQL at-rest encryption depends on infrastructure. Operators must enable disk encryption for production. |
| **Redis** | Idempotency keys; enable Redis TLS in production. |
| **RabbitMQ** | Event bus; use TLS for AMQP connections. |
| **Gateway API** | Stripe API calls use TLS; keys transmitted only over secure channels. |

**Note:** Encryption of sensitive payment-related fields at rest (e.g., for `customer_ref` or invoice buyer data) is planned. Current protection relies on database and disk-level encryption.

---

## 9. Compliance references

py-payments-ledger follows privacy and payment security principles aligned with:

| Framework | Relevant principles |
|-----------|---------------------|
| **PCI-DSS** | No cardholder data storage; reduced scope via gateway delegation |
| **LGPD** (Brazil) | Purpose limitation, data minimization, transparency, security |
| **GDPR** (EU) | Lawfulness, purpose limitation, storage limitation, integrity and confidentiality |

Key capabilities:

- **No card data** — All sensitive payment data delegated to gateway
- **Audit trail** of payments, refunds, and access denials
- **Configurable retention** with export capabilities
- **ABAC/RBAC** — default-deny access control
- **Tenant isolation** — data never shared across tenants

For full compliance documentation, see [`docs/compliance.md`](compliance.md).

---

## 10. Contact for data requests

For data access, rectification, deletion or portability requests, contact the platform operator:

- **Email:** privacy@fluxe.io
- **Subject line:** `[Data Request] — <tenant name or ID>`
- **Expected response time:** 15 business days (aligned with LGPD Art. 18, §5)

The operator is the data controller. py-payments-ledger acts as the data processor providing payment and ledger services for the B2B platform.
