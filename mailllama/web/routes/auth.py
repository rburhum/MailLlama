"""Gmail OAuth2 routes."""

from __future__ import annotations

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...models import Account
from ...providers.gmail_api import build_oauth_flow
from ..deps import get_db

router = APIRouter()


@router.get("/gmail/start")
def gmail_start(request: Request) -> RedirectResponse:
    settings = get_settings()
    if not (settings.gmail_client_id and settings.gmail_client_secret):
        raise HTTPException(400, "GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET not configured.")
    flow = build_oauth_flow(
        settings.gmail_client_id, settings.gmail_client_secret, settings.gmail_redirect_uri
    )
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    # Persist PKCE code_verifier and state in the session cookie so the
    # callback can complete the exchange.
    request.session["oauth_state"] = state
    request.session["code_verifier"] = flow.code_verifier
    return RedirectResponse(url)


@router.get("/gmail/callback", response_class=HTMLResponse)
def gmail_callback(request: Request, code: str, session: Session = Depends(get_db)) -> HTMLResponse:
    import html
    import logging
    import traceback

    log = logging.getLogger(__name__)
    settings = get_settings()
    if not settings.secret_key:
        raise HTTPException(500, "SECRET_KEY not set — cannot encrypt tokens.")

    try:
        flow = build_oauth_flow(
            settings.gmail_client_id,
            settings.gmail_client_secret,
            settings.gmail_redirect_uri,
        )
        # Restore the PKCE code_verifier that was generated when the
        # authorization URL was built in /auth/gmail/start.
        flow.code_verifier = request.session.pop("code_verifier", None)
        flow.fetch_token(code=code)
        creds = flow.credentials
        creds_json = creds.to_json()

        # Resolve email via a one-off provider call.
        from ...providers.gmail_api import GmailAPIProvider

        p = GmailAPIProvider(creds_json)
        email = p.email

        f = Fernet(settings.secret_key.encode())
        blob = f.encrypt(creds_json.encode()).decode()

        acct = session.scalar(select(Account).where(Account.email == email))
        if acct is None:
            acct = Account(provider="gmail_api", email=email, oauth_blob=blob)
            session.add(acct)
        else:
            acct.oauth_blob = blob
            acct.provider = "gmail_api"
        session.commit()
    except Exception as exc:  # noqa: BLE001
        log.exception("Gmail OAuth callback failed")
        details = html.escape(f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}")
        return HTMLResponse(
            f"""
            <html><body style="font-family:sans-serif;padding:2rem;max-width:900px;">
              <h2 style="color:#991b1b;">Gmail OAuth failed</h2>
              <pre style="background:#f3f4f6;padding:1rem;white-space:pre-wrap;">{details}</pre>
              <p><a href="/auth/gmail/start">Try again</a> or check the server logs.</p>
            </body></html>
            """,
            status_code=500,
        )

    # Clear remaining session state.
    request.session.pop("oauth_state", None)

    return HTMLResponse(
        f"""
        <html><body style="font-family:sans-serif;padding:2rem;">
          <h2>Connected: {html.escape(email)}</h2>
          <p><a href="/">Go to dashboard</a></p>
        </body></html>
        """
    )
