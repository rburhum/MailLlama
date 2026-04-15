# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

MailLlama is a single-user FastAPI web app that drives a Gmail / IMAP inbox to zero with help from an OpenAI-compatible LLM. See `README.md` for the feature list.

## Commands

This project is managed with [uv](https://docs.astral.sh/uv/). `uv.lock` is the source of truth for dependency versions.

```bash
# Install (from lockfile)
uv sync --extra dev

# Run tests
uv run pytest -q
uv run pytest tests/test_classify.py::test_classify_senders_writes_labels  # single test

# Run the app
uv run mailllama setup        # interactive .env setup (also edits .gitignore)
uv run mailllama init         # apply Alembic migrations
uv run mailllama serve        # FastAPI on BIND_HOST:BIND_PORT
uv run mailllama sync         # pull mail → DB (wraps in optional SSH tunnel)
uv run mailllama classify     # LLM-classify senders (wraps in optional SSH tunnel)
uv run mailllama tunnel       # open the configured SSH tunnel and hold

# Database migrations
uv run alembic revision --autogenerate -m "msg"   # new migration
uv run alembic upgrade head

# Lint
uv run ruff check .
```

CI (`.github/workflows/tests.yml`) runs `uv sync --frozen --extra dev --python 3.13 && uv run pytest -q`. `--frozen` means **any change to `pyproject.toml` requires regenerating `uv.lock` and committing it** or CI will fail.

## Architecture

### Provider abstraction (`mailllama/providers/`)
All mail I/O goes through `MailProvider` (ABC in `providers/base.py`). Two implementations: `gmail_api.py` (OAuth2, labels, threads, batch ops) and `imap.py` (imapclient; folder-based, less capable). Services never touch the Gmail/IMAP libs directly — they take a `MailProvider`. `providers/factory.py:provider_for(account)` decrypts the stored OAuth blob (Fernet, keyed by `SECRET_KEY`) and builds the right instance.

### LLM client (`mailllama/llm/`)
`client.py:LLMClient` is an OpenAI SDK pointed at `LLM_BASE_URL` (local vLLM / llama.cpp / Ollama `/v1`, or later an Anthropic-compatible endpoint) — nothing is hardcoded to a specific vendor. `complete_json` retries and tolerantly parses JSON (strips code fences, finds first balanced object), and falls back to a request without `response_format` if the server rejects it.

### Classification is **sender-level and cached** (`services/classify.py`)
This is the biggest cost lever. We never send every message to the LLM. Instead: aggregate into the `sender` table (one row per `from_addr`), batch up to `batch_size` senders per prompt (default 20), and cache results by SHA1(sender+subjects+counts+model) in the KV cache for 14 days. A re-classification only hits the LLM for senders whose aggregate fingerprint changed. The prompt (`llm/prompts.py:SENDER_BATCH_SYSTEM`) asks for strict JSON. If you add a new label, update `LABELS` and `LABEL_GUIDE` in `llm/prompts.py` — it affects both the prompt and the UI filters.

### Pluggable infra (`config.py`, `db.py`, `cache.py`, `tasks/runner.py`)
Everything is driven by pydantic-settings reading `.env`:
- **DB**: `DATABASE_URL` — SQLite default, Postgres via `postgresql+psycopg://…`. `db.py` uses `StaticPool` for `:memory:` so tests see consistent state across threads.
- **Cache**: `cache.py:get_cache()` returns a `RedisCache` if `REDIS_URL` is set, else `SqliteCache` backed by the `kv_cache` table.
- **Tasks**: `tasks/runner.py:submit(kind, fn)` inserts a `task` row and either runs on the current asyncio loop (UI) or `asyncio.run`s synchronously (CLI). Progress is updated via `TaskHandle.update(progress=…, message=…)` and polled by HTMX every 2s. Redis/Huey is stubbed as an optional extra; the abstraction is in place if we need durable task state later.

### SSH tunnel (`mailllama/ssh_tunnel.py`)
`maybe_ssh_tunnel()` is a context manager that, if `SSH_TUNNEL_ENABLED=true`, spawns `ssh -N -T -o ExitOnForwardFailure=yes -L …` and waits up to 15 s for the local port. It reuses an already-open local port (so a manual `ssh -fN` doesn't conflict). It is wired into the FastAPI lifespan (`web/app.py:_lifespan`) and around the `sync` / `classify` / `tunnel` CLI commands. The user's SSH client must be able to connect non-interactively (keys / agent / `~/.ssh/config` alias) — no password prompting is supported.

### "Mailing list I've never replied to" detection (`services/interaction.py`)
Runs per `Thread`: asks the provider for sent-messages in that thread (Gmail: `label:SENT` on the thread; IMAP: header search on References/In-Reply-To in the Sent folder), sets `Thread.user_has_replied`, then rolls that up into `Sender.reply_count`. The Subscriptions → Untouched tab filters on `reply_count == 0`.

### Unsubscribe flow (`services/unsubscribe.py`)
Strict priority: (1) RFC 8058 one-click POST when `List-Unsubscribe-Post: List-Unsubscribe=One-Click` is present, (2) surface the https link to the UI without auto-GETing it (avoids tracking/confirmation traps), (3) mailto unsubscribe via the provider, (4) LLM body-link extraction fallback — surfaced to the user, never auto-clicked.

### Safety rails to preserve when changing actions (`services/actions.py`)
- Destructive actions use Gmail **Trash** (30-day reversible), never permanent delete.
- `DRY_RUN=true` short-circuits mail-side calls and still writes an `action_log` row.
- Whitelist rules override blacklist rules in `services/rules.py:evaluate_message` — don't reorder.
- Rules are advisory in v1; we do not auto-execute destructive actions on sync.

## Templates

`web/templates/*.html` extends `base.html` (Tailwind + HTMX via CDN, no build step). Routes render via `Jinja2Templates.TemplateResponse(request, name, ctx)` — **first positional arg is `request`**; the old `TemplateResponse(name, {"request": request, …})` signature is deprecated in current Starlette and will blow up with "unhashable type: dict".

## Git workflow

- **Never push.** Do not run `git push` (or any remote-mutating command like `git push --force`, `gh pr merge`, release-creating commands) unless the user explicitly tells you to in that turn. Prior approval does not carry over.
- **Commit messages must be descriptive.** The subject line should summarize the change and the body should explain *what* changed and *why* — enough that reading the commit alone makes the intent clear without diffing. Short one-liners like "fix bug" or "update code" are not acceptable.

## Tests

`tests/fakes.py:FakeMailProvider` is the canonical in-memory provider for tests — use it instead of mocking the real Gmail/IMAP libs. `tests/test_classify.py` shows the stub-LLM pattern (`monkeypatch.setattr("mailllama.services.classify.LLMClient", _StubLLM)`) — prefer this over HTTP-level mocks.

`tests/conftest.py` sets `DATABASE_URL=sqlite:///:memory:` and a throwaway `SECRET_KEY` **before importing the app**, and the `session` fixture creates/drops tables per test. Don't lift those to module scope or tests will leak state.
