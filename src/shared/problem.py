from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from .correlation import get_correlation_id


@dataclass(frozen=True)
class ProblemDetails:
    title: str
    status: int
    detail: str
    instance: str
    correlation_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "instance": self.instance,
            "correlation_id": self.correlation_id,
        }


def http_problem(status: int, title: str, detail: str, instance: str) -> HTTPException:
    pd = ProblemDetails(
        title=title,
        status=status,
        detail=detail,
        instance=instance,
        correlation_id=get_correlation_id(),
    )
    return HTTPException(status_code=status, detail=pd.to_dict())
