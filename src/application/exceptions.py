from __future__ import annotations


class DomainError(Exception):
    """Base domain error with HTTP status semantics."""

    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class PaymentNotFoundError(DomainError):
    def __init__(self, payment_id: str):
        super().__init__(f"Payment intent not found: {payment_id}", 404)


class InvalidPaymentStateError(DomainError):
    def __init__(self, current: str, attempted: str):
        super().__init__(f"Cannot transition from {current} to {attempted}", 409)


class LedgerImbalanceError(DomainError):
    def __init__(self, entry_id: str, debit_sum: object, credit_sum: object):
        super().__init__(
            f"Ledger entry {entry_id} is imbalanced: debits={debit_sum}, credits={credit_sum}",
            422,
        )


class TenantNotFoundError(DomainError):
    def __init__(self, tenant_id: str):
        super().__init__(f"Tenant not found: {tenant_id}", 404)


class InsufficientFundsError(DomainError):
    def __init__(self, message: str = "Insufficient funds"):
        super().__init__(message, 402)
