"""Auth endpoints and the dependency every user-scoped route depends on.

The session cookie is HttpOnly (JavaScript cannot read it, so an XSS cannot
exfiltrate it), SameSite=Lax (a cross-site POST cannot ride it, which is CSRF
protection for the state-changing routes), and Secure whenever the app is not
being served over plain HTTP locally.
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from api.auth.service import (
    SESSION_COOKIE,
    SESSION_TTL,
    AuthError,
    authenticate,
    create_session,
    create_user,
    destroy_session,
    user_for_session,
)
from api.db.pool import get_pool

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Secure cookies require HTTPS; local dev is http://localhost.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "").lower() in {"1", "true", "yes"}


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class UserOut(BaseModel):
    id: int
    email: str


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )


def current_user(sieve_session: Annotated[str | None, Cookie()] = None) -> dict[str, Any]:
    """FastAPI dependency: the signed-in user, or 401.

    Used by every route that touches user-owned data. Returning 401 rather
    than redirecting keeps the API honest about what it is — the frontend
    decides what a signed-out user sees.
    """
    with get_pool().connection() as conn:
        user = user_for_session(conn, sieve_session)
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


#: The dependency every user-scoped route takes. Annotated form rather than
#: a `Depends()` default: it is FastAPI's current documented style and it
#: keeps the signature a normal type annotation, so B008 stays on for the
#: real mutable-default bugs it is meant to catch.
CurrentUser = Annotated[dict[str, Any], Depends(current_user)]


def optional_user(
    sieve_session: Annotated[str | None, Cookie()] = None,
) -> dict[str, Any] | None:
    with get_pool().connection() as conn:
        return user_for_session(conn, sieve_session)


@router.post("/signup", status_code=201)
def signup(body: Credentials, response: Response) -> UserOut:
    with get_pool().connection() as conn:
        try:
            user_id = create_user(conn, body.email, body.password)
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        token = create_session(conn, user_id)
    _set_cookie(response, token)
    return UserOut(id=user_id, email=body.email.strip().lower())


@router.post("/login")
def login(body: Credentials, response: Response) -> UserOut:
    with get_pool().connection() as conn:
        try:
            user_id = authenticate(conn, body.email, body.password)
        except AuthError as exc:
            # 401 with the same message for both failure modes.
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        token = create_session(conn, user_id)
    _set_cookie(response, token)
    return UserOut(id=user_id, email=body.email.strip().lower())


@router.post("/logout", status_code=204)
def logout(
    response: Response, sieve_session: Annotated[str | None, Cookie()] = None
) -> Response:
    with get_pool().connection() as conn:
        destroy_session(conn, sieve_session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return Response(status_code=204)


@router.get("/me")
def me(user: CurrentUser) -> UserOut:
    return UserOut(**user)
