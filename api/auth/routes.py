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


def password_signup_enabled() -> bool:
    """Whether new email/password accounts may be created.

    OFF by default, because verification cannot complete: Resend refuses every
    address except the account owner until a sending domain is verified, so the
    flow would hand a real person a code that can never arrive. A path that
    cannot finish is worse than no path.

    ONE SWITCH — set PASSWORD_SIGNUP=1 when the domain is verified and the whole
    flow returns. Nothing is deleted and the tests still cover it. Read at call
    time rather than import time so it is configurable per-test and per-deploy
    without a restart ordering problem.
    """
    return os.environ.get("PASSWORD_SIGNUP", "").lower() in {"1", "true", "yes"}


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class UserOut(BaseModel):
    id: int
    email: str
    email_verified: bool = False
    # How the code actually went out, so the UI can say something true rather
    # than "check your inbox" when nothing was sent there.
    delivery: str | None = None


class VerifyBody(BaseModel):
    code: str = Field(min_length=4, max_length=12)


PENDING_COOKIE = "sieve_pending"


class AuthConfig(BaseModel):
    """What sign-in methods this deployment actually supports.

    The frontend asks rather than assuming: a clone without Google
    credentials must hide the button, not show one that 500s, and a
    deployment that cannot deliver mail must not offer a code flow.
    """

    google: bool
    email_transport: str
    password_signup: bool
    # Existing password accounts must still be able to sign in even while new
    # ones are closed — disabling signup is not a reason to lock people out.
    password_login: bool = True


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
    # Enforced HERE, not only in the UI. A disabled path that a curl can still
    # walk through is not disabled, and this one would create accounts that can
    # never verify.
    if not password_signup_enabled():
        raise HTTPException(
            status_code=403,
            detail=(
                "Email sign-up is temporarily unavailable: we cannot deliver "
                "verification codes until our sending domain is verified. "
                "Please continue with Google."
            ),
        )
    with get_pool().connection() as conn:
        try:
            user_id = create_user(conn, body.email, body.password)
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        code = codes.issue(conn, user_id)
        # A PENDING token, not a session. It authenticates the /verify and
        # /resend calls and NOTHING else — no collections, no /me, no access
        # of any kind. Issuing a real session here and relying on the UI to
        # send the user to a code screen was security theatre: anyone could
        # register an address they do not own, ignore the screen, and have a
        # working account.
        pending = create_session(conn, user_id, pending=True)
    # Sent after the transaction so a delivery failure cannot roll back a
    # created account — the user can always request another code.
    delivered = send_code(body.email.strip().lower(), code)
    response.set_cookie(
        PENDING_COOKIE,
        pending,
        max_age=30 * 60,
        httponly=True,
        samesite=COOKIE_SAMESITE,  # type: ignore[arg-type]
        secure=COOKIE_SECURE,
        path="/",
    )
    out = UserOut(id=user_id, email=body.email.strip().lower())
    out.delivery = delivered
    return out


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
    return AuthConfig(
        google=google.configured(),
        email_transport=transport(),
        password_signup=password_signup_enabled(),
    )


def _pending_user(conn: Any, token: str | None) -> dict[str, Any]:
    """The half-authenticated user mid-verification.

    Accepts a pending token OR a real session, so an account created before
    the gate existed can still finish verifying. Anything else is a 401.
    """
    user = user_for_session(conn, token)
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


@router.post("/verify")
def verify_email(
    body: VerifyBody,
    response: Response,
    sieve_pending: Annotated[str | None, Cookie()] = None,
    sieve_session: Annotated[str | None, Cookie()] = None,
) -> UserOut:
    with get_pool().connection() as conn:
        user = _pending_user(conn, sieve_pending or sieve_session)
        try:
            codes.verify(conn, user["id"], body.code.strip())
        except codes.CodeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # The real session is minted HERE and nowhere else. Proving control of
        # the address is what grants access — that is the whole point, and it
        # is why signup deliberately hands back nothing usable.
        destroy_session(conn, sieve_pending)
        token = create_session(conn, user["id"])
        fresh = user_for_session_by_id(conn, user["id"])
    _set_cookie(response, token)
    response.delete_cookie(PENDING_COOKIE, path="/")
    return UserOut(**fresh)


@router.post("/verify/resend", status_code=202)
def resend_code(
    sieve_pending: Annotated[str | None, Cookie()] = None,
    sieve_session: Annotated[str | None, Cookie()] = None,
) -> dict[str, str]:
    with get_pool().connection() as conn:
        user = _pending_user(conn, sieve_pending or sieve_session)
        if user.get("email_verified"):
            raise HTTPException(status_code=400, detail="Already verified")
        code = codes.issue(conn, user["id"])
    delivered = send_code(user["email"], code)
    return {"status": delivered, "transport": transport()}


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
