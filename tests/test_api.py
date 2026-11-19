import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("dd")
    cfg = tmp / "test.yaml"
    cfg.write_text(f"""
customer_name: "Test Co"
docs_dir: "{FIXTURES}"
index_dir: "{tmp / 'index'}"
api_keys: ["test-key"]
log_file: "{tmp / 'requests.jsonl'}"
""")
    os.environ["DEPLOYDESK_CONFIG"] = str(cfg)
    import importlib
    import deploydesk.api as api
    importlib.reload(api)
    from fastapi.testclient import TestClient
    c = TestClient(api.app)
    c.tmp = tmp
    return c


H = {"X-API-Key": "test-key"}


def test_health_before_ingest(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "no_index"


def test_ask_needs_key(client):
    assert client.post("/ask", json={"question": "anything"}).status_code == 401
    assert client.post("/ask", json={"question": "anything"}, headers={"X-API-Key": "wrong"}).status_code == 401


def test_ask_before_ingest_is_409(client):
    assert client.post("/ask", json={"question": "refund policy"}, headers=H).status_code == 409


def test_ingest(client):
    r = client.post("/ingest", json={}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["documents"] == 3
    assert body["chunks"] >= 3


def test_ask_returns_citation_to_right_doc(client):
    r = client.post("/ask", json={"question": "How many days do customers have to request a refund?"}, headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["citations"], body
    assert body["citations"][0]["source"] == "refunds.md"
    assert "30 days" in body["answer"]
    assert set(body["timing_ms"]) == {"retrieval", "total"}


def test_ask_html_and_pdf_sources(client):
    r = client.post("/ask", json={"question": "what port does the on-prem gateway listen on", "mode": "bm25"}, headers=H)
    assert r.json()["retrieved"][0]["source"] == "gateway.html"


@pytest.mark.parametrize("mode", ["vector", "bm25", "hybrid"])
def test_modes(client, mode):
    r = client.post("/ask", json={"question": "refund window", "mode": mode, "top_k": 2}, headers=H)
    assert r.status_code == 200
    assert len(r.json()["retrieved"]) <= 2


def test_bad_mode_rejected(client):
    r = client.post("/ask", json={"question": "refund window", "mode": "magic"}, headers=H)
    assert r.status_code == 422


def test_request_log_and_metrics(client):
    log = client.tmp / "requests.jsonl"
    assert log.exists() and "/ask" in log.read_text()
    m = client.get("/metrics", headers=H).json()
    assert m["/ask"]["count"] >= 1
    assert m["/ask"]["p50_ms"] is not None


def test_ui_and_stats(client):
    assert "DeployDesk" in client.get("/").text
    assert client.get("/dashboard").status_code == 200
    assert client.get("/stats").json()["chunks"] > 0
