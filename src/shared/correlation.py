from __future__ import annotations

import contextvars
import uuid

correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)
tenant_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("tenant_id", default="")
subject_var: contextvars.ContextVar[str] = contextvars.ContextVar("subject", default="")


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def set_correlation_id(value: str) -> None:
    correlation_id_var.set(value)


def get_correlation_id() -> str:
    v = correlation_id_var.get()
    return v or ""


def set_tenant_id(value: str) -> None:
    tenant_id_var.set(value)


def get_tenant_id() -> str:
    v = tenant_id_var.get()
    return v or ""


def set_subject(value: str) -> None:
    subject_var.set(value)


def get_subject() -> str:
    v = subject_var.get()
    return v or ""
