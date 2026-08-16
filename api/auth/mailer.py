"""Delivering codes.

No SMTP dependency and no email provider SDK. The project runs on a free
tier and an unconfigured mail service would be a hard failure at signup, so
delivery is an interface with two implementations:

  console   the default. Logs the code as structured JSON. Development and
            the demo deploy both use this, and the frontend surfaces the code
            in the response ONLY when this transport is active — see the
            explicit guard in routes.py, which is the single place that
            decision is made.
  smtp      env-configured. Used when SMTP_HOST is set.

The console transport is honest rather than fake: it does not pretend an
email was sent. A reviewer opening the demo can still complete the flow,
which is the point of having verification in a portfolio project at all.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

_log = logging.getLogger("sieve.auth")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
MAIL_FROM = os.environ.get("MAIL_FROM", "no-reply@sieve.local")


def transport() -> str:
    return "smtp" if SMTP_HOST else "console"


def send_code(to: str, code: str) -> str:
    if not SMTP_HOST:
        # Structured, so it is greppable in `docker compose logs` and shows up
        # in the same stream as every request line.
        _log.info("email_code_issued", extra={"to": to, "code": code, "transport": "console"})
        return "console"

    msg = EmailMessage()
    msg["Subject"] = "Your Sieve verification code"
    msg["From"] = MAIL_FROM
    msg["To"] = to
    msg.set_content(f"Your Sieve verification code is {code}.\n\nIt expires in 10 minutes.")
    # FAIL LOUDLY. A silently swallowed SMTP error is why a code can never
    # arrive while the UI cheerfully says it was sent — the user then waits
    # for an email that was never accepted by anyone.
    try:
        with smtplib.SMTP(SMTP_HOST, int(os.environ.get("SMTP_PORT", "587")), timeout=15) as s:
            s.starttls()
            user, password = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
            if user and password:
                s.login(user, password)
            s.send_message(msg)
    except Exception as exc:
        # Resend (and most providers) reject recipients outside the account
        # owner's own address until a sending domain is verified, so this is
        # the EXPECTED path before a domain exists — and it has to surface.
        _log.warning("email_code_failed", extra={"to": to, "error": str(exc)[:200]})
        return f"failed: {type(exc).__name__}"
    _log.info("email_code_issued", extra={"to": to, "transport": "smtp"})
    return "sent"
