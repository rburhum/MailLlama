"""Build the configured provider for an account."""

from __future__ import annotations

from cryptography.fernet import Fernet

from ..config import get_settings
from ..models import Account
from .base import MailProvider
from .gmail_api import GmailAPIProvider
from .imap import IMAPProvider


def provider_for(account: Account) -> MailProvider:
    settings = get_settings()
    if account.provider == "gmail_api":
        if not settings.secret_key:
            raise RuntimeError("SECRET_KEY is required to decrypt Gmail tokens.")
        if not account.oauth_blob:
            raise RuntimeError(f"Account {account.email} has no stored credentials.")
        f = Fernet(settings.secret_key.encode())
        creds_json = f.decrypt(account.oauth_blob.encode()).decode()
        return GmailAPIProvider(creds_json, email=account.email)
    if account.provider == "imap":
        if not (settings.imap_user and settings.imap_password):
            raise RuntimeError("IMAP_USER and IMAP_PASSWORD must be set.")
        return IMAPProvider(
            host=account.imap_host or settings.imap_host,
            user=settings.imap_user,
            password=settings.imap_password,
            port=settings.imap_port,
        )
    raise ValueError(f"Unknown provider: {account.provider}")
