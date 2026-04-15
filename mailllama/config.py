"""Application settings loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "sqlite:///./mailllama.db"

    # Cache / queue
    redis_url: str | None = None

    # LLM (OpenAI-compatible)
    llm_base_url: str = "http://127.0.0.1:8080/v1"
    llm_api_key: str = "local"
    llm_model: str = "gemma4:26b"
    llm_model_classify: str | None = None  # falls back to llm_model

    # Optional SSH tunnel
    # When enabled, MailLlama runs: ssh -N -L <local_port>:<remote_host>:<remote_port> <host>
    # before issuing LLM calls. The tunnel is torn down on exit. Your SSH client
    # must already be able to connect to ``ssh_tunnel_host`` without a password
    # (keys / agent / config alias).
    ssh_tunnel_enabled: bool = False
    ssh_tunnel_host: str | None = None            # e.g. "tenerife" (ssh alias)
    ssh_tunnel_local_port: int = 11434
    ssh_tunnel_remote_host: str = "127.0.0.1"     # from the server's POV
    ssh_tunnel_remote_port: int = 11434
    ssh_tunnel_extra_args: str = ""                # raw extra flags, rarely used

    # Mail
    mail_provider: Literal["gmail_api", "imap"] = "gmail_api"

    gmail_client_id: str | None = None
    gmail_client_secret: str | None = None
    gmail_redirect_uri: str = "http://127.0.0.1:8000/auth/gmail/callback"

    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_user: str | None = None
    imap_password: str | None = None

    # Security
    secret_key: str = Field(default="", description="Fernet key for token encryption")

    # Web
    bind_host: str = "127.0.0.1"
    bind_port: int = 8000
    web_auth_token: str | None = None

    # Behavior
    dry_run: bool = False

    @property
    def classify_model(self) -> str:
        return self.llm_model_classify or self.llm_model

    @property
    def uses_redis(self) -> bool:
        return bool(self.redis_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
