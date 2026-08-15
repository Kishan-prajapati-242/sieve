"""Google sign-in, as the authorization-code flow with PKCE-style state.

No SDK. The flow is three HTTP calls and hand-writing it keeps the security
decisions visible instead of buried in a library's defaults:

  1. redirect the browser to Google with a `state` we generated
  2. Google redirects back with `code` and that same `state`
  3. exchange `code` for an id_token, server side, over TLS

`state` is the CSRF defence and is REQUIRED, not advisory: without it an
attacker can hand a victim a crafted callback URL and link the victim's
browser to the attacker's Google account. It is stored in a short-lived
HttpOnly cookie and compared on return.

Accounts are keyed on the provider's `sub`, never on email. An email address
can be released and re-registered by a different person; `sub` is stable for
the life of the Google account, and matching on email is precisely how
takeover-by-recycled-address happens.

The id_token is verified by asking Google's tokeninfo endpoint rather than by
validating the JWT signature locally. That is one extra round trip and it
avoids shipping a JWKS cache and signature verification we would then have to
keep correct — the wrong place to be clever.
"""

from __future__ import annotations

import os
import secrets
from typing import Any

import httpx

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")
APP_ORIGIN = os.environ.get("APP_ORIGIN", "http://localhost:5173")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"

STATE_COOKIE = "sieve_oauth_state"


def configured() -> bool:
    """Whether Google sign-in is available.

    Checked by the frontend so the button is hidden rather than shown and
    broken when credentials are absent — which is the default for anyone who
    clones this repo.
    """
    return bool(CLIENT_ID and CLIENT_SECRET)


def new_state() -> str:
    return secrets.token_urlsafe(24)


def authorize_url(state: str) -> str:
    from urllib.parse import urlencode

    return f"{AUTH_URL}?" + urlencode(
        {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": "openid email",
            "state": state,
            # Ask for the account chooser rather than silently reusing a
            # signed-in Google session the visitor may not have intended.
            "prompt": "select_account",
        }
    )


async def exchange(code: str) -> dict[str, Any]:
    """Trade the code for a verified identity. Returns {sub, email}."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        token_resp = await client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "redirect_uri": REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )
        token_resp.raise_for_status()
        id_token = token_resp.json().get("id_token")
        if not id_token:
            raise ValueError("Google returned no id_token")

        info = await client.get(TOKENINFO_URL, params={"id_token": id_token})
        info.raise_for_status()
        claims = info.json()

    # tokeninfo validates the signature and expiry; the audience check is
    # ours, and skipping it would accept a token minted for a DIFFERENT app.
    if claims.get("aud") != CLIENT_ID:
        raise ValueError("id_token audience mismatch")
    if not claims.get("sub"):
        raise ValueError("id_token carries no subject")
    return {"sub": claims["sub"], "email": (claims.get("email") or "").lower()}
