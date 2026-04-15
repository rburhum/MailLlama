"""Smoke test for the FastAPI app."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client():
    from mailllama.web.app import app

    return TestClient(app)


def test_health():
    r = _client().get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_dashboard_no_account_renders(session):
    r = _client().get("/")
    assert r.status_code == 200
    assert "Connect Gmail" in r.text


def test_tasks_page_renders(session):
    r = _client().get("/tasks/")
    assert r.status_code == 200


def test_senders_requires_account(session):
    r = _client().get("/senders/")
    assert r.status_code == 404
