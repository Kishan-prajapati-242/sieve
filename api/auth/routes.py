"""Auth endpoints and the dependency every user-scoped route depends on.

The session cookie is HttpOnly (JavaScript cannot read it, so an XSS cannot
exfiltrate it), SameSite=Lax (a cross-site POST cannot ride it, which is CSRF
protection for the state-changing routes), and Secure whenever the app is not
being served over plain HTTP locally.
"""

from __future__ import annotations

import os
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from api.auth import codes, google
from api.auth.mailer import send_code, transport
from api.auth.service import (
    SESSION_COOKIE,
    SESSION_TTL,
    AuthError,
    authenticate,
    create_session,
    create_user,
    destroy_session,
    link_or_create_google_user,
    user_for_session,
    user_for_session_by_id,
)
from api.db.pool import get_pool

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Secure cookies require HTTPS; local dev is http://localhost.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "").lower() in {"1", "true", "yes"}

# Frontend and API are on DIFFERENT origins in production (Vercel + Render),
# which makes every API call cross-site. SameSite=Lax is sent only on
# top-level navigation, so a Lax cookie would be omitted from every fetch and
# the user would appear signed out immediately after signing in. "none"
# requires Secure, which is why the two are validated together.
COOKIE_SAMESITE = os.environ.get("COOKIE_SAMESITE", "lax").lower()
if COOKIE_SAMESITE == "none" and not COOKIE_SECURE:
    raise RuntimeError(
        "COOKIE_SAMESITE=none requires COOKIE_SECURE=1 (browsers reject it otherwise)"
    )


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class UserOut(BaseModel):
    id: int
    email: str
    email_verified: bool = False


class VerifyBody(BaseModel):
    code: str = Field(min_length=4, max_length=12)


class AuthConfig(BaseModel):
    """What sign-in methods this deployment actually supports.

    The frontend asks rather than assuming: a clone without Google
    credentials must hide the button, not show one that 500s.
    """

    google: bool
    email_transport: str


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite=COOKIE_SAMESITE,  # type: ignore[arg-type]
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
        code = codes.issue(conn, user_id)
        token = create_session(conn, user_id)
    # Sent after the transaction so a delivery failure cannot roll back a
    # created account — the user can always request another code.
    send_code(body.email.strip().lower(), code)
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
def logout(response: Response, sieve_session: Annotated[str | None, Cookie()] = None) -> Response:
    with get_pool().connection() as conn:
        destroy_session(conn, sieve_session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return Response(status_code=204)


@router.get("/me")
def me(user: CurrentUser) -> UserOut:
    return UserOut(**user)


@router.get("/config")
def config() -> AuthConfig:
    return AuthConfig(google=google.configured(), email_transport=transport())


@router.post("/verify")
def verify_email(body: VerifyBody, user: CurrentUser) -> UserOut:
    with get_pool().connection() as conn:
        try:
            codes.verify(conn, user["id"], body.code.strip())
        except codes.CodeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        fresh = user_for_session_by_id(conn, user["id"])
    return UserOut(**fresh)


@router.post("/verify/resend", status_code=202)
def resend_code(user: CurrentUser) -> dict[str, str]:
    with get_pool().connection() as conn:
        if user.get("email_verified"):
            raise HTTPException(status_code=400, detail="Already verified")
        code = codes.issue(conn, user["id"])
    send_code(user["email"], code)
    return {"status": "sent", "transport": transport()}


# ---------------------------------------------------------------- Google ---


@router.get("/google/start")
def google_start() -> RedirectResponse:
    if not google.configured():
        raise HTTPException(status_code=404, detail="Google sign-in is not configured")
    state = google.new_state()
    resp = RedirectResponse(google.authorize_url(state), status_code=307)
    # Short-lived, HttpOnly: this is the CSRF token for the callback and the
    # browser must not be able to read or script it.
    resp.set_cookie(
        google.STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        samesite=COOKIE_SAMESITE,  # type: ignore[arg-type]
        secure=COOKIE_SECURE,
        path="/",
    )
    return resp


@router.get("/google/callback")
async def google_callback(request: Request) -> RedirectResponse:
    if not google.configured():
        raise HTTPException(status_code=404, detail="Google sign-in is not configured")

    expected = request.cookies.get(google.STATE_COOKIE)
    state = request.query_params.get("state")
    code = request.query_params.get("code")
    # Both must be present AND equal. A missing cookie is a failure, not a
    # reason to skip the check — that shortcut is the whole vulnerability.
    if not expected or not state or not secrets.compare_digest(expected, state):
        return RedirectResponse(f"{google.APP_ORIGIN}/login?error=state", status_code=307)
    if not code:
        return RedirectResponse(f"{google.APP_ORIGIN}/login?error=denied", status_code=307)

    try:
        identity = await google.exchange(code)
    except Exception:
        return RedirectResponse(f"{google.APP_ORIGIN}/login?error=exchange", status_code=307)

    with get_pool().connection() as conn:
        user_id = link_or_create_google_user(conn, identity["sub"], identity["email"])
        token = create_session(conn, user_id)

    resp = RedirectResponse(f"{google.APP_ORIGIN}/collections", status_code=307)
    resp.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite=COOKIE_SAMESITE,  # type: ignore[arg-type]
        secure=COOKIE_SECURE,
        path="/",
    )
    resp.delete_cookie(google.STATE_COOKIE, path="/")
    return resp
