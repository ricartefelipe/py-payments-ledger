from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from src.api.deps.db import get_db
from src.application.security import authenticate_and_issue_token, build_principal, decode_token
from src.shared.problem import http_problem

router = APIRouter(prefix="/v1", tags=["auth"])


class TokenRequest(BaseModel):
    email: EmailStr
    password: str
    tenantId: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int


@router.post("/auth/token", response_model=TokenResponse)
def token(req: TokenRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    settings = request.app.state.settings
    res = authenticate_and_issue_token(db, settings, req.email, req.password, req.tenantId)
    return TokenResponse(access_token=res.access_token, token_type=res.token_type, expires_in=res.expires_in)


@router.get("/me")
def me(request: Request, authorization: str | None = Header(default=None, alias="Authorization")):
    if not authorization or not authorization.startswith("Bearer "):
        raise http_problem(401, "Unauthorized", "Missing bearer token", instance="/v1/me")
    token = authorization.removeprefix("Bearer ").strip()
    settings = request.app.state.settings
    claims = decode_token(settings, token)
    principal = build_principal(claims)
    return {
        "sub": principal.sub,
        "tid": principal.tid,
        "roles": principal.roles,
        "perms": principal.perms,
        "plan": principal.plan,
        "region": principal.region,
        "jti": principal.jti,
    }
