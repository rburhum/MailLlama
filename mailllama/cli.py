"""MailLlama CLI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer
import uvicorn

from .config import get_settings
from .ssh_tunnel import maybe_ssh_tunnel

app = typer.Typer(no_args_is_help=True, add_completion=False)

PROJECT_ROOT = Path.cwd()
ENV_PATH = PROJECT_ROOT / ".env"
GITIGNORE_PATH = PROJECT_ROOT / ".gitignore"


@app.command()
def init() -> None:
    """Create the database and apply migrations."""
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"])
    raise typer.Exit(result.returncode)


@app.command()
def serve(reload: bool = typer.Option(False, "--reload")) -> None:
    """Run the web UI."""
    settings = get_settings()
    uvicorn.run(
        "mailllama.web.app:app",
        host=settings.bind_host,
        port=settings.bind_port,
        reload=reload,
    )


@app.command()
def sync() -> None:
    """Sync the connected mailbox into the local DB."""
    from sqlalchemy import select

    from .db import session_scope
    from .models import Account
    from .providers.factory import provider_for
    from .services.sync import sync_account

    with maybe_ssh_tunnel(), session_scope() as session:
        acct = session.scalar(select(Account).order_by(Account.id).limit(1))
        if acct is None:
            typer.echo("No account connected. Run: mailllama auth gmail")
            raise typer.Exit(1)
        p = provider_for(acct)
        n = sync_account(session, acct, p)
        typer.echo(f"Synced {n} messages for {acct.email}.")


@app.command()
def classify() -> None:
    """Classify senders using the configured LLM."""
    from sqlalchemy import select

    from .db import session_scope
    from .models import Account
    from .services.classify import classify_senders

    with maybe_ssh_tunnel(), session_scope() as session:
        acct = session.scalar(select(Account).order_by(Account.id).limit(1))
        if acct is None:
            typer.echo("No account connected.")
            raise typer.Exit(1)
        n = classify_senders(session, acct)
        typer.echo(f"Classified {n} senders.")


@app.command()
def tunnel() -> None:
    """Open the configured SSH tunnel and hold it until Ctrl-C.

    Useful for ad-hoc LLM calls / curl testing while the tunnel is up.
    """
    import signal

    settings = get_settings()
    if not settings.ssh_tunnel_enabled:
        typer.echo("SSH_TUNNEL_ENABLED is false. Run `mailllama setup` to configure one.")
        raise typer.Exit(1)
    with maybe_ssh_tunnel() as t:
        typer.echo(f"Tunnel up: 127.0.0.1:{t.local_port} -> {t.remote}")
        typer.echo("Press Ctrl-C to stop.")
        try:
            signal.pause()
        except KeyboardInterrupt:
            typer.echo("Shutting down tunnel.")


@app.command("auth")
def auth(provider: str = typer.Argument(..., help="gmail")) -> None:
    """Start an OAuth flow (gmail)."""
    if provider != "gmail":
        typer.echo("Only 'gmail' is supported by the CLI auth command.")
        raise typer.Exit(1)
    settings = get_settings()
    typer.echo(
        "Start the web UI and visit "
        f"http://{settings.bind_host}:{settings.bind_port}/auth/gmail/start\n"
        "to complete Gmail OAuth. MailLlama will store an encrypted refresh token."
    )


@app.command()
def setup(
    env_file: Path = typer.Option(
        ENV_PATH, "--env-file", "-f", help="Path to the .env file to write."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite without prompting."),
) -> None:
    """Interactively create (or rewrite) the .env config file.

    Stores secrets locally in a .env file which is added to .gitignore
    automatically. Generates a Fernet SECRET_KEY for you.
    """
    from cryptography.fernet import Fernet

    env_file = env_file.expanduser().resolve()
    existing = _read_env(env_file) if env_file.exists() else {}

    if existing and not force:
        if not typer.confirm(f"{env_file} exists. Keep existing values as defaults?", default=True):
            existing = {}

    typer.secho("\nMailLlama setup\n", fg=typer.colors.CYAN, bold=True)
    typer.echo("Press Enter to accept the default in [brackets]. Secrets are hidden.\n")

    answers: dict[str, str] = {}

    # ----- Database -----
    typer.secho("Database", fg=typer.colors.BRIGHT_BLUE, bold=True)
    answers["DATABASE_URL"] = typer.prompt(
        "Database URL",
        default=existing.get("DATABASE_URL", "sqlite:///./mailllama.db"),
    )

    # ----- Redis -----
    use_redis = typer.confirm(
        "\nUse Redis for cache / task queue?",
        default=bool(existing.get("REDIS_URL")),
    )
    if use_redis:
        answers["REDIS_URL"] = typer.prompt(
            "REDIS_URL",
            default=existing.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
        )
    else:
        answers["REDIS_URL"] = ""

    # ----- SSH tunnel -----
    typer.secho("\nSSH tunnel to LLM server", fg=typer.colors.BRIGHT_BLUE, bold=True)
    typer.echo(
        "If your LLM runs on a remote box you reach with an ssh alias\n"
        "(e.g. `ssh tenerife`), MailLlama can open the tunnel for you."
    )
    tunnel_on = typer.confirm(
        "Enable SSH tunnel?",
        default=_env_bool(existing.get("SSH_TUNNEL_ENABLED"), default=False),
    )
    answers["SSH_TUNNEL_ENABLED"] = "true" if tunnel_on else "false"

    default_local = existing.get("SSH_TUNNEL_LOCAL_PORT", "11434")
    default_remote_port = existing.get("SSH_TUNNEL_REMOTE_PORT", "11434")
    if tunnel_on:
        answers["SSH_TUNNEL_HOST"] = typer.prompt(
            "SSH host alias (e.g. 'tenerife')",
            default=existing.get("SSH_TUNNEL_HOST", ""),
        )
        answers["SSH_TUNNEL_LOCAL_PORT"] = typer.prompt("Local port", default=default_local)
        answers["SSH_TUNNEL_REMOTE_HOST"] = typer.prompt(
            "Remote host (from server's POV)",
            default=existing.get("SSH_TUNNEL_REMOTE_HOST", "127.0.0.1"),
        )
        answers["SSH_TUNNEL_REMOTE_PORT"] = typer.prompt(
            "Remote port", default=default_remote_port
        )
        answers["SSH_TUNNEL_EXTRA_ARGS"] = typer.prompt(
            "Extra ssh args (optional)",
            default=existing.get("SSH_TUNNEL_EXTRA_ARGS", ""),
            show_default=False,
        )
    else:
        answers["SSH_TUNNEL_HOST"] = existing.get("SSH_TUNNEL_HOST", "")
        answers["SSH_TUNNEL_LOCAL_PORT"] = default_local
        answers["SSH_TUNNEL_REMOTE_HOST"] = existing.get("SSH_TUNNEL_REMOTE_HOST", "127.0.0.1")
        answers["SSH_TUNNEL_REMOTE_PORT"] = default_remote_port
        answers["SSH_TUNNEL_EXTRA_ARGS"] = existing.get("SSH_TUNNEL_EXTRA_ARGS", "")

    # ----- LLM -----
    typer.secho("\nLLM (OpenAI-compatible endpoint)", fg=typer.colors.BRIGHT_BLUE, bold=True)
    default_base = existing.get(
        "LLM_BASE_URL",
        f"http://127.0.0.1:{answers['SSH_TUNNEL_LOCAL_PORT']}/v1"
        if tunnel_on
        else "http://127.0.0.1:11434/v1",
    )
    answers["LLM_BASE_URL"] = typer.prompt("LLM base URL", default=default_base)
    answers["LLM_API_KEY"] = typer.prompt(
        "LLM API key", default=existing.get("LLM_API_KEY", "local")
    )
    answers["LLM_MODEL"] = typer.prompt(
        "LLM model", default=existing.get("LLM_MODEL", "gemma4:26b")
    )
    answers["LLM_MODEL_CLASSIFY"] = typer.prompt(
        "Faster classification model (optional)",
        default=existing.get("LLM_MODEL_CLASSIFY", ""),
        show_default=False,
    )

    # ----- Mail provider -----
    typer.secho("\nMail provider", fg=typer.colors.BRIGHT_BLUE, bold=True)
    provider = typer.prompt(
        "Provider (gmail_api / imap)",
        default=existing.get("MAIL_PROVIDER", "gmail_api"),
    ).strip().lower()
    while provider not in ("gmail_api", "imap"):
        provider = typer.prompt("Provider must be 'gmail_api' or 'imap'").strip().lower()
    answers["MAIL_PROVIDER"] = provider

    if provider == "gmail_api":
        typer.echo(
            "\nCreate an OAuth client at https://console.cloud.google.com/apis/credentials\n"
            "(type: Web application; redirect URI: http://127.0.0.1:8000/auth/gmail/callback)."
        )
        answers["GMAIL_CLIENT_ID"] = typer.prompt(
            "GMAIL_CLIENT_ID",
            default=existing.get("GMAIL_CLIENT_ID", ""),
        )
        answers["GMAIL_CLIENT_SECRET"] = typer.prompt(
            "GMAIL_CLIENT_SECRET",
            default=existing.get("GMAIL_CLIENT_SECRET", ""),
            hide_input=True,
        )
        answers["GMAIL_REDIRECT_URI"] = typer.prompt(
            "GMAIL_REDIRECT_URI",
            default=existing.get(
                "GMAIL_REDIRECT_URI", "http://127.0.0.1:8000/auth/gmail/callback"
            ),
        )
        # Preserve any existing IMAP values without prompting.
        for k in ("IMAP_HOST", "IMAP_PORT", "IMAP_USER", "IMAP_PASSWORD"):
            answers[k] = existing.get(k, "")
    else:
        answers["IMAP_HOST"] = typer.prompt(
            "IMAP_HOST", default=existing.get("IMAP_HOST", "imap.gmail.com")
        )
        answers["IMAP_PORT"] = typer.prompt(
            "IMAP_PORT", default=existing.get("IMAP_PORT", "993")
        )
        answers["IMAP_USER"] = typer.prompt(
            "IMAP_USER", default=existing.get("IMAP_USER", "")
        )
        answers["IMAP_PASSWORD"] = typer.prompt(
            "IMAP_PASSWORD",
            default=existing.get("IMAP_PASSWORD", ""),
            hide_input=True,
        )
        for k in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REDIRECT_URI"):
            answers[k] = existing.get(k, "")

    # ----- Security -----
    typer.secho("\nSecurity", fg=typer.colors.BRIGHT_BLUE, bold=True)
    secret = existing.get("SECRET_KEY", "").strip()
    if not secret or typer.confirm("Generate a new SECRET_KEY (invalidates stored tokens)?", default=not secret):
        secret = Fernet.generate_key().decode()
        typer.echo("Generated a new SECRET_KEY.")
    answers["SECRET_KEY"] = secret

    answers["WEB_AUTH_TOKEN"] = typer.prompt(
        "Optional bearer token for the web UI (leave blank to disable)",
        default=existing.get("WEB_AUTH_TOKEN", ""),
        show_default=False,
        hide_input=True,
    )

    # ----- Web -----
    answers["BIND_HOST"] = typer.prompt(
        "\nBind host", default=existing.get("BIND_HOST", "127.0.0.1")
    )
    answers["BIND_PORT"] = typer.prompt("Bind port", default=existing.get("BIND_PORT", "8000"))

    # ----- Behavior -----
    dry = typer.confirm(
        "Start in DRY_RUN mode (log destructive actions instead of executing)?",
        default=_env_bool(existing.get("DRY_RUN"), default=False),
    )
    answers["DRY_RUN"] = "true" if dry else "false"

    # ----- Write file -----
    if env_file.exists() and not force:
        if not typer.confirm(f"\nWrite config to {env_file}?", default=True):
            typer.echo("Aborted; nothing written.")
            raise typer.Exit(1)

    _write_env(env_file, answers)
    typer.secho(f"\nWrote {env_file}", fg=typer.colors.GREEN)

    # ----- .gitignore hygiene -----
    _ensure_gitignore(env_file)

    typer.secho("\nNext steps:", fg=typer.colors.CYAN, bold=True)
    typer.echo("  mailllama init        # create the database")
    if answers["MAIL_PROVIDER"] == "gmail_api":
        typer.echo("  mailllama serve       # then visit /auth/gmail/start to connect")
    else:
        typer.echo("  mailllama sync        # pulls mail via IMAP")
    if tunnel_on:
        typer.echo("  mailllama tunnel      # open the SSH tunnel on demand")


def _env_bool(raw: str | None, *, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


# Order preserved so the written file is easy to diff and read.
_ENV_ORDER: list[tuple[str, str]] = [
    ("# ----- Database -----", ""),
    ("DATABASE_URL", ""),
    ("", ""),
    ("# ----- Cache / task queue (optional) -----", ""),
    ("REDIS_URL", ""),
    ("", ""),
    ("# ----- SSH tunnel -----", ""),
    ("SSH_TUNNEL_ENABLED", ""),
    ("SSH_TUNNEL_HOST", ""),
    ("SSH_TUNNEL_LOCAL_PORT", ""),
    ("SSH_TUNNEL_REMOTE_HOST", ""),
    ("SSH_TUNNEL_REMOTE_PORT", ""),
    ("SSH_TUNNEL_EXTRA_ARGS", ""),
    ("", ""),
    ("# ----- LLM -----", ""),
    ("LLM_BASE_URL", ""),
    ("LLM_API_KEY", ""),
    ("LLM_MODEL", ""),
    ("LLM_MODEL_CLASSIFY", ""),
    ("", ""),
    ("# ----- Mail provider -----", ""),
    ("MAIL_PROVIDER", ""),
    ("GMAIL_CLIENT_ID", ""),
    ("GMAIL_CLIENT_SECRET", ""),
    ("GMAIL_REDIRECT_URI", ""),
    ("IMAP_HOST", ""),
    ("IMAP_PORT", ""),
    ("IMAP_USER", ""),
    ("IMAP_PASSWORD", ""),
    ("", ""),
    ("# ----- Security -----", ""),
    ("SECRET_KEY", ""),
    ("WEB_AUTH_TOKEN", ""),
    ("", ""),
    ("# ----- Web -----", ""),
    ("BIND_HOST", ""),
    ("BIND_PORT", ""),
    ("", ""),
    ("# ----- Behavior -----", ""),
    ("DRY_RUN", ""),
]


def _write_env(path: Path, answers: dict[str, str]) -> None:
    lines: list[str] = []
    for key, _ in _ENV_ORDER:
        if key == "":
            lines.append("")
        elif key.startswith("#"):
            lines.append(key)
        else:
            value = answers.get(key, "")
            lines.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _ensure_gitignore(env_file: Path) -> None:
    """Make sure secrets won't get committed."""
    gi = GITIGNORE_PATH
    patterns_needed = {".env", ".env.local", "tokens/", "mailllama.db"}

    # If the env file lives outside the repo root, add its basename too.
    try:
        env_file.relative_to(PROJECT_ROOT)
    except ValueError:
        # Not under project root — nothing to add to .gitignore.
        return

    existing = set()
    if gi.exists():
        existing = {line.strip() for line in gi.read_text().splitlines() if line.strip()}
    missing = sorted(patterns_needed - existing)
    if not missing:
        return
    with gi.open("a") as f:
        if gi.exists() and not gi.read_text().endswith("\n"):
            f.write("\n")
        f.write("\n# MailLlama secrets (auto-added by `mailllama setup`)\n")
        for p in missing:
            f.write(p + "\n")
    typer.echo(f"Updated {gi} with: {', '.join(missing)}")


if __name__ == "__main__":
    app()
