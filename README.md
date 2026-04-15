# MailLlama

Drive your Gmail (or any IMAP mailbox) to inbox zero with help from an LLM.

MailLlama classifies your senders, surfaces subscriptions and newsletters you
never read, flags the mail taking up the most space, and lets you bulk archive,
trash, or unsubscribe from everything in a few clicks.

## Features

- Classify senders as spam / subscription / newsletter / promo / transactional / personal
- Detect mailing lists you have **never replied to** and offer to wipe them
- One-click unsubscribe (RFC 8058) and mailto unsubscribe
- Size report — biggest senders and biggest individual messages
- Blacklist / whitelist rules (whitelist always wins)
- Batch archive, trash, and move
- Web UI (FastAPI + HTMX, no frontend build step)
- Gmail API or IMAP — same UI either way
- Optional SSH tunnel to a remote LLM box
- SQLite + in-process tasks by default; Postgres and Redis are optional drop-ins

## Getting it running

With [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv sync                     # install from uv.lock
uv run mailllama setup      # interactive config + creates the database
uv run mailllama serve      # open http://127.0.0.1:8000
```

Without uv:

```bash
pip install -e . && mailllama setup && mailllama serve
```

Then visit `/auth/gmail/start` in the browser to connect your mailbox.
Re-running `mailllama setup` is safe — it loads your existing values as
defaults, preserves your `SECRET_KEY`, and only applies pending migrations.
`uv sync --extra dev` also installs the test suite (`uv run pytest`).

### Remote LLM over SSH

If your LLM runs on another machine reachable with an SSH alias (e.g. you can
already `ssh tenerife`), answer **yes** to "Enable SSH tunnel?" during `setup`.
MailLlama will open the tunnel every time it needs the LLM and close it on
exit.
