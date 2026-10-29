"""Classificacao de falhas de gateway: recusa, falha transitoria ou incerteza.

Os adapters capturavam ``Exception`` e devolviam ``success=False`` para tudo.
Com isso, um timeout depois do envio — em que o provedor pode ter autorizado a
cobranca — chegava ao dominio indistinguivel de uma recusa por cartao sem
limite. O dominio marcava o pagamento como falho e um reenvio posterior emitia
uma operacao nova, com chave nova: cobranca duplicada.

A regra que separa os casos e **se a requisicao chegou a ser enviada**:

* nao conectou (DNS, connection refused, TLS) -> o provedor nao viu nada.
  Falha transitoria, seguro reenviar com a mesma chave.
* enviou e nao recebeu resposta (timeout de leitura, conexao cortada) -> pode
  ter sido processado. **Incerto.**
* respondeu 5xx -> pode ter processado antes de falhar. **Incerto.**
* respondeu 429 -> nao processou; respeitar o backoff. Transitorio.
* respondeu 4xx -> recusa explicita. Definitivo.

Operacoes idempotentes de leitura (``get_status``, ``list_*``) nunca sao
incertas: nao produzem efeito, logo podem ser repetidas livremente.
"""

from __future__ import annotations

import httpx

from src.application.ports.payment_gateway import GatewayResult, GatewayStatus
from src.shared.logging import get_logger

log = get_logger(__name__)

#: Erros em que a requisicao nao chegou a sair da maquina.
_NOT_SENT: tuple[type[BaseException], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.UnsupportedProtocol,
    httpx.InvalidURL,
    httpx.ProxyError,
)

#: Erros apos o envio: o provedor pode ter processado.
_SENT_NO_ANSWER: tuple[type[BaseException], ...] = (
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
)


def classify_exception(
    exc: BaseException,
    *,
    operation: str,
    gateway_ref: str = "",
    mutating: bool = True,
) -> GatewayResult:
    """Traduz uma excecao de transporte no desfecho correspondente.

    ``mutating=False`` para consultas: sem efeito colateral, nunca incerto.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return classify_status(
            exc.response.status_code,
            operation=operation,
            gateway_ref=gateway_ref,
            mutating=mutating,
            detail=_safe_detail(exc),
        )

    if isinstance(exc, _NOT_SENT):
        log.warning(
            "gateway unreachable; request was not sent",
            extra={"operation": operation, "error": type(exc).__name__},
        )
        return GatewayResult(
            success=False,
            gateway_ref=gateway_ref,
            status=GatewayStatus.FAILED,
            error_code="gateway_unreachable",
            error_message=f"{operation}: could not reach the gateway",
            is_retryable=True,
            is_uncertain=False,
        )

    if isinstance(exc, _SENT_NO_ANSWER | httpx.TimeoutException):
        if not mutating:
            return GatewayResult(
                success=False,
                gateway_ref=gateway_ref,
                status=GatewayStatus.FAILED,
                error_code="gateway_timeout",
                error_message=f"{operation}: no answer from the gateway",
                is_retryable=True,
                is_uncertain=False,
            )
        log.error(
            "gateway did not answer after the request was sent; outcome is unknown",
            extra={"operation": operation, "error": type(exc).__name__},
        )
        return GatewayResult(
            success=False,
            gateway_ref=gateway_ref,
            status=GatewayStatus.UNCERTAIN,
            error_code="gateway_timeout_after_send",
            error_message=(
                f"{operation}: request was sent but no answer arrived; "
                "the operation may have been accepted"
            ),
            is_retryable=False,
            is_uncertain=True,
        )

    log.error(
        "unexpected gateway failure",
        extra={"operation": operation, "error": type(exc).__name__},
        exc_info=True,
    )
    # Causa desconhecida numa operacao com efeito: tratar como incerto e a
    # escolha segura. Classificar como recusa arriscaria cobrar de novo.
    return GatewayResult(
        success=False,
        gateway_ref=gateway_ref,
        status=GatewayStatus.UNCERTAIN if mutating else GatewayStatus.FAILED,
        error_code="gateway_error",
        error_message=f"{operation}: unexpected gateway failure",
        is_retryable=False,
        is_uncertain=mutating,
    )


def classify_status(
    status_code: int,
    *,
    operation: str,
    gateway_ref: str = "",
    mutating: bool = True,
    detail: str = "",
) -> GatewayResult:
    """Classifica uma resposta HTTP de erro do provedor."""
    if status_code == 429:
        return GatewayResult(
            success=False,
            gateway_ref=gateway_ref,
            status=GatewayStatus.FAILED,
            error_code="rate_limited",
            error_message=f"{operation}: gateway rate limited",
            is_retryable=True,
            is_uncertain=False,
        )
    if status_code >= 500:
        return GatewayResult(
            success=False,
            gateway_ref=gateway_ref,
            status=GatewayStatus.UNCERTAIN if mutating else GatewayStatus.FAILED,
            error_code=f"gateway_{status_code}",
            error_message=f"{operation}: gateway returned {status_code}",
            is_retryable=not mutating,
            is_uncertain=mutating,
        )
    return GatewayResult(
        success=False,
        gateway_ref=gateway_ref,
        status=GatewayStatus.FAILED,
        error_code=f"gateway_{status_code}",
        error_message=f"{operation}: gateway rejected the request ({status_code}){detail}",
        is_retryable=False,
        is_uncertain=False,
    )


def _safe_detail(exc: httpx.HTTPStatusError) -> str:
    """Trecho curto do corpo do erro, sem arrastar payload inteiro para o log."""
    try:
        body = exc.response.text
    except Exception:  # noqa: BLE001 - corpo ilegivel nao pode mascarar o erro HTTP
        return ""
    if not body:
        return ""
    return f": {body[:200]}"
