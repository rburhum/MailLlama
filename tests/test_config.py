"""Tests for Settings validators.

Empty-string env values must not crash int fields — a stale .env with a
line like ``IMAP_PORT=`` (written by an older setup flow, or hand-edited
to blank) should fall back to the field default.
"""

from __future__ import annotations

from mailllama.config import Settings


def test_empty_string_int_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("IMAP_PORT", "")
    monkeypatch.setenv("BIND_PORT", "")
    monkeypatch.setenv("SSH_TUNNEL_LOCAL_PORT", "")
    monkeypatch.setenv("SSH_TUNNEL_REMOTE_PORT", "")

    s = Settings(_env_file=None)  # ignore any .env on disk
    assert s.imap_port == 993
    assert s.bind_port == 8000
    assert s.ssh_tunnel_local_port == 11434
    assert s.ssh_tunnel_remote_port == 11434


def test_non_empty_int_is_respected(monkeypatch):
    monkeypatch.setenv("BIND_PORT", "10000")
    s = Settings(_env_file=None)
    assert s.bind_port == 10000
