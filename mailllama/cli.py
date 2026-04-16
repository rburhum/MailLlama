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
    """Apply database migrations.

    `mailllama setup` already runs this for you. Use `init` directly when
    you only want to apply migrations (e.g. after pulling new code that
    added a migration) without touching .env.
    """
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
    skip_migrations: bool = typer.Option(
        False, "--skip-migrations", help="Do not apply database migrations at the end."
    ),
) -> None:
    """Interactively configure MailLlama and set up the database.

    Safe to re-run: existing values in .env are loaded as defaults (press
    Enter to keep each one), and Alembic migrations are idempotent. Secrets
    already present — like SECRET_KEY — are preserved; this command will
    never rotate them for you (that would invalidate stored OAuth tokens).
    """
    from cryptography.fernet import Fernet

    env_file = env_file.expanduser().resolve()
    existing = _read_env(env_file) if env_file.exists() else {}

    typer.secho("\nMailLlama setup\n", fg=typer.colors.CYAN, bold=True)
    if existing:
        typer.echo(
            f"Loaded existing config from {env_file}.\n"
            "Press Enter to keep each value. Secrets are hidden.\n"
        )
    else:
        typer.echo("Press Enter to accept the default in [brackets]. Secrets are hidden.\n")

    answers: dict[str, str] = {}

    # ----- Database -----
    typer.secho("Database", fg=typer.colors.BRIGHT_BLUE, bold=True)
    answers["DATABASE_URL"] = typer.prompt(
        "Database URL",
        default=existing.get("DATABASE_URL", "sqlite:///mailllama.db"),
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

    # ----- Web (collected before Gmail so we can compose the redirect URI) -----
    typer.secho("\nWeb server", fg=typer.colors.BRIGHT_BLUE, bold=True)
    answers["BIND_HOST"] = typer.prompt(
        "Bind host", default=existing.get("BIND_HOST", "127.0.0.1")
    )
    answers["BIND_PORT"] = typer.prompt(
        "Bind port", default=existing.get("BIND_PORT", "8000")
    )
    default_redirect_uri = (
        f"http://{answers['BIND_HOST']}:{answers['BIND_PORT']}/auth/gmail/callback"
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
            "(type: Web application). Add this exact URL under\n"
            "'Authorized redirect URIs':\n"
            f"    {default_redirect_uri}"
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
        # Default to the URI composed from BIND_HOST:BIND_PORT so it can't
        # silently drift out of sync when the user changes the bind port.
        existing_redirect = existing.get("GMAIL_REDIRECT_URI", "").strip()
        redirect_default = existing_redirect or default_redirect_uri
        if existing_redirect and existing_redirect != default_redirect_uri:
            typer.secho(
                "\nNote: your existing GMAIL_REDIRECT_URI "
                f"({existing_redirect}) does not match your bind port "
                f"({answers['BIND_PORT']}).\n"
                f"Expected: {default_redirect_uri}",
                fg=typer.colors.YELLOW,
            )
        answers["GMAIL_REDIRECT_URI"] = typer.prompt(
            "GMAIL_REDIRECT_URI",
            default=redirect_default,
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
    if not secret:
        secret = Fernet.generate_key().decode()
        typer.echo("Generated a new SECRET_KEY (saved to .env).")
    else:
        typer.echo("Keeping existing SECRET_KEY (required to decrypt stored OAuth tokens).")
    answers["SECRET_KEY"] = secret

    answers["WEB_AUTH_TOKEN"] = typer.prompt(
        "Optional bearer token for the web UI (leave blank to disable)",
        default=existing.get("WEB_AUTH_TOKEN", ""),
        show_default=False,
        hide_input=True,
    )

    # ----- Behavior -----
    dry = typer.confirm(
        "Start in DRY_RUN mode (log destructive actions instead of executing)?",
        default=_env_bool(existing.get("DRY_RUN"), default=False),
    )
    answers["DRY_RUN"] = "true" if dry else "false"

    # ----- Write file -----
    _write_env(env_file, answers)
    typer.secho(f"\nWrote {env_file}", fg=typer.colors.GREEN)

    # ----- .gitignore hygiene -----
    _ensure_gitignore(env_file)

    # ----- Database -----
    if skip_migrations:
        typer.echo("\nSkipping database migrations (--skip-migrations).")
    else:
        _apply_migrations(answers["DATABASE_URL"])

    typer.secho("\nSetup complete.", fg=typer.colors.GREEN, bold=True)
    typer.secho("Next steps:", fg=typer.colors.CYAN, bold=True)
    if answers["MAIL_PROVIDER"] == "gmail_api":
        typer.echo("  mailllama serve       # then visit /auth/gmail/start to connect")
    else:
        typer.echo("  mailllama sync        # pulls mail via IMAP")
    if tunnel_on:
        typer.echo("  mailllama tunnel      # open the SSH tunnel on demand")


def _apply_migrations(database_url: str) -> None:
    """Run `alembic upgrade head`, reporting whether the DB already existed."""
    typer.secho("\nDatabase", fg=typer.colors.BRIGHT_BLUE, bold=True)

    if database_url.startswith("sqlite:///"):
        # SQLAlchemy SQLite URL forms:
        #   sqlite:///foo.db         → relative path 'foo.db'
        #   sqlite:////abs/foo.db    → absolute path '/abs/foo.db'
        #   sqlite:///:memory:       → in-memory DB (no file)
        raw = database_url[len("sqlite:///") :]
        if raw == ":memory:":
            typer.echo("Using in-memory SQLite database.")
        else:
            db_path = Path(raw).expanduser().resolve()
            if db_path.exists():
                typer.echo(f"Found existing database at {db_path}.")
                typer.echo("Applying any pending migrations...")
            else:
                typer.echo(f"Creating database at {db_path}...")
    else:
        typer.echo(f"Applying migrations against {database_url}...")

    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"])
    if result.returncode != 0:
        typer.secho(
            "\nMigration failed. Fix the issue and re-run `mailllama init`.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(result.returncode)
    typer.secho("Database up to date.", fg=typer.colors.GREEN)


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
    """Write answers to ``path`` in the order defined by ``_ENV_ORDER``.

    Keys with empty-string values are omitted entirely: pydantic-settings then
    falls back to the field default, which is what we want for things like
    ``IMAP_PORT`` when the user picked Gmail API. Writing ``IMAP_PORT=`` with
    no value would make pydantic try to parse ``""`` as an int and crash.
    """
    lines: list[str] = []
    for key, _ in _ENV_ORDER:
        if key == "":
            lines.append("")
        elif key.startswith("#"):
            lines.append(key)
        else:
            value = answers.get(key, "")
            if value == "":
                continue  # skip empty values — let pydantic use the default
            lines.append(f"{key}={value}")
    # Collapse runs of blank lines created by skipped section entries.
    cleaned: list[str] = []
    for ln in lines:
        if ln == "" and cleaned and cleaned[-1] == "":
            continue
        cleaned.append(ln)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(cleaned).rstrip() + "\n")
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
