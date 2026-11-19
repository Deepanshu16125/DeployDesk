import io
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parent / "fixtures"
H = {"X-API-Key": "test-key"}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("dd")
    docs = tmp / "docs"
    docs.mkdir()
    for f in FIXTURES.iterdir():
        (docs / f.name).write_bytes(f.read_bytes())
    cfg = tmp / "test.yaml"
    cfg.write_text(f"""
customer_name: "Test Co"
docs_dir: "{docs}"
index_dir: "{tmp / 'index'}"
db_path: "{tmp / 'test.db'}"
api_keys: ["test-key"]
generator: extractive
rerank: true
log_file: "{tmp / 'requests.jsonl'}"
""")
    os.environ["DEPLOYDESK_CONFIG"] = str(cfg)
    import importlib
    import deploydesk.api as api
    importlib.reload(api)
    c = TestClient(api.app)
    c.tmp = tmp
    return c


def stream(client, question, **extra):
    r = client.post("/api/ask/stream", json={"question": question, **extra}, headers=H)
    assert r.status_code == 200, r.text
    events = {}
    tokens = []
    for block in r.text.strip().split("\n\n"):
        ev = block.split("\n")[0].removeprefix("event: ")
        data = json.loads(block.split("\n")[1].removeprefix("data: "))
        if ev == "token":
            tokens.append(data["t"])
        events[ev] = data
    return events, "".join(tokens)


def test_health_before_ingest(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "no_index"


def test_auth_required_everywhere(client):
    for method, path in [("post", "/ask"), ("post", "/api/ask/stream"), ("get", "/api/conversations"),
                         ("get", "/api/tickets"), ("get", "/api/documents"), ("get", "/api/analytics"),
                         ("get", "/api/settings"), ("post", "/ingest")]:
        r = client.post(path, json={"question": "anything"}) if method == "post" else client.get(path)
        assert r.status_code == 401, path
    assert client.post("/ask", json={"question": "x y z"}, headers={"X-API-Key": "wrong"}).status_code == 401


def test_public_config_hides_key_unless_demo(client):
    cfg = client.get("/api/config").json()
    assert cfg["customer_name"] == "Test Co"
    assert "demo_key" not in cfg


def test_ask_before_ingest_is_409(client):
    assert client.post("/ask", json={"question": "refund policy"}, headers=H).status_code == 409


def test_ingest(client):
    r = client.post("/ingest", json={}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["documents"] == 3
    docs = client.get("/api/documents", headers=H).json()
    assert {d["source"] for d in docs} == {"refunds.md", "gateway.html", "sla.pdf"}
    assert all(d["status"] == "indexed" and d["chunks"] > 0 for d in docs)


def test_ask_json_cites_right_doc(client):
    r = client.post("/ask", json={"question": "How many days do customers have to request a refund?"}, headers=H)
    body = r.json()
    assert body["citations"][0]["source"] == "refunds.md"
    assert "30 days" in body["answer"]
    assert body["confidence"] in ("high", "medium", "low")
    assert set(body["timing_ms"]) == {"retrieval", "total"}


def test_guardrail_refuses_off_topic(client):
    body = client.post("/ask", json={"question": "Who won the football world cup in 2022?"}, headers=H).json()
    assert body["confidence"] == "none"
    assert body["citations"] == []
    assert body["answer"].startswith("I couldn't find")


@pytest.mark.parametrize("mode", ["vector", "bm25", "hybrid"])
def test_modes(client, mode):
    r = client.post("/ask", json={"question": "refund window", "mode": mode, "top_k": 2}, headers=H)
    assert r.status_code == 200
    assert len(r.json()["retrieved"]) <= 2


def test_bad_mode_rejected(client):
    assert client.post("/ask", json={"question": "refund window", "mode": "magic"}, headers=H).status_code == 422


def test_stream_saves_conversation(client):
    events, text = stream(client, "What port does the on-prem gateway listen on?")
    assert set(events) >= {"meta", "token", "done"}
    assert events["meta"]["retrieved"][0]["text"]
    done = events["done"]
    assert "8443" in done["answer"] and done["citations"]
    cid = done["conversation_id"]
    conv = client.get(f"/api/conversations/{cid}", headers=H).json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    # follow-up goes into the same conversation
    events2, _ = stream(client, "And what uptime does the SLA promise?", conversation_id=cid)
    assert events2["done"]["conversation_id"] == cid
    assert len(client.get(f"/api/conversations/{cid}", headers=H).json()["messages"]) == 4
    client.cid, client.mid = cid, done["message_id"]


def test_feedback(client):
    assert client.post("/api/feedback", json={"message_id": client.mid, "rating": -1, "comment": "too vague"}, headers=H).status_code == 200
    assert client.post("/api/feedback", json={"message_id": "nope", "rating": 1}, headers=H).status_code == 404
    assert client.post("/api/feedback", json={"message_id": client.mid, "rating": 5}, headers=H).status_code == 422
    fb = client.get("/api/analytics", headers=H).json()["feedback"]
    assert fb[0]["comment"] == "too vague"


def test_ticket_lifecycle(client):
    t = client.post("/api/tickets", json={"conversation_id": client.cid, "message_id": client.mid,
                                          "subject": "Gateway port blocked", "requester": "ops@test.co",
                                          "priority": "high"}, headers=H).json()
    assert t["status"] == "open" and "8443" in t["bot_answer"] and "gateway" in t["question"].lower()
    t2 = client.patch(f"/api/tickets/{t['id']}", json={"status": "in_progress", "assignee": "Priya",
                                                        "note": "checking firewall"}, headers=H).json()
    assert t2["status"] == "in_progress" and t2["assignee"] == "Priya" and t2["notes"][0]["text"] == "checking firewall"
    assert client.patch(f"/api/tickets/{t['id']}", json={"status": "done"}, headers=H).status_code == 422
    assert client.patch("/api/tickets/9999", json={"status": "resolved"}, headers=H).status_code == 404
    conv = client.get(f"/api/conversations/{client.cid}", headers=H).json()
    assert conv["tickets"][0]["id"] == t["id"]


def test_upload_search_delete(client):
    md = b"# Billing\n\n## Seat pricing\n\nEach additional seat on the Team plan costs 12 dollars per month, billed annually.\n"
    r = client.post("/api/documents", files=[("files", ("seat pricing.md", io.BytesIO(md), "text/markdown")),
                                              ("files", ("evil.exe", io.BytesIO(b"MZ"), "application/octet-stream"))], headers=H)
    res = {x["source"]: x for x in r.json()["results"]}
    assert res["uploads/seat-pricing.md"]["status"] == "indexed"
    assert res["uploads/evil.exe"]["status"] == "rejected"
    body = client.post("/ask", json={"question": "how much does an extra seat cost on the team plan"}, headers=H).json()
    assert body["citations"][0]["source"] == "uploads/seat-pricing.md"
    chunks = client.get("/api/documents/chunks", params={"source": "uploads/seat-pricing.md"}, headers=H).json()
    assert "12 dollars" in chunks[0]["text"]
    d = client.delete("/api/documents", params={"source": "uploads/seat-pricing.md"}, headers=H).json()
    assert d["chunks_removed"] >= 1
    assert "uploads/seat-pricing.md" not in {x["source"] for x in client.get("/api/documents", headers=H).json()}


def test_settings_roundtrip(client):
    s = client.put("/api/settings", json={"top_k": 3, "retrieval_mode": "bm25", "suggested_questions": ["a", " ", "b"]}, headers=H).json()
    assert s["top_k"] == 3 and s["retrieval_mode"] == "bm25" and s["suggested_questions"] == ["a", "b"]
    assert client.put("/api/settings", json={"api_keys": ["x"]}, headers=H).status_code == 400
    assert client.put("/api/settings", json={"retrieval_mode": "magic"}, headers=H).status_code == 400
    client.put("/api/settings", json={"top_k": 5, "retrieval_mode": "hybrid"}, headers=H)


def test_analytics_and_metrics(client):
    a = client.get("/api/analytics", headers=H).json()
    assert a["counts"]["questions"] >= 2 and a["counts"]["tickets_total"] >= 1
    assert 12 <= len(a["volume"]) <= 24
    m = client.get("/metrics", headers=H).json()
    assert m["/ask"]["count"] >= 1 and m["/api/ask/stream"]["count"] >= 2
    assert (client.tmp / "requests.jsonl").exists()


def test_ui_served(client):
    assert "app.js" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
