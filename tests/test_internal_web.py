from __future__ import annotations

from fastapi.testclient import TestClient

from internal_web.main import app


def test_health_no_auth_required() -> None:
    c = TestClient(app)
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_index_no_basic_auth_ok() -> None:
    c = TestClient(app)
    r = c.get("/")
    assert r.status_code == 200
    assert "客户评估" in r.text
