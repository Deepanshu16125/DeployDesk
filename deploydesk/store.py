from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, title TEXT, created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY, conversation_id TEXT, role TEXT, content TEXT,
    citations TEXT, retrieved TEXT, meta TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT, rating INTEGER, comment TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, message_id TEXT, subject TEXT,
    question TEXT, bot_answer TEXT, requester TEXT, priority TEXT, status TEXT, assignee TEXT,
    notes TEXT, created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT, status INTEGER, latency_ms REAL,
    ttft_ms REAL, tokens_per_s REAL, generator TEXT, top_score REAL, created_at REAL
);
CREATE TABLE IF NOT EXISTS documents (
    source TEXT PRIMARY KEY, kind TEXT, bytes INTEGER, chunks INTEGER, status TEXT,
    error TEXT, ingested_at REAL
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, created_at);
"""


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    # conversations / messages
    def new_conversation(self, title: str) -> str:
        cid = uuid.uuid4().hex[:10]
        now = time.time()
        self._exec("INSERT INTO conversations VALUES (?,?,?,?)", (cid, title[:120], now, now))
        return cid

    def conversation_exists(self, cid: str) -> bool:
        return bool(self._all("SELECT 1 FROM conversations WHERE id=?", (cid,)))

    def add_message(self, cid: str, role: str, content: str, citations=None, retrieved=None, meta=None) -> str:
        mid = uuid.uuid4().hex[:12]
        now = time.time()
        self._exec("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)",
                   (mid, cid, role, content, json.dumps(citations or []), json.dumps(retrieved or []),
                    json.dumps(meta or {}), now))
        self._exec("UPDATE conversations SET updated_at=? WHERE id=?", (now, cid))
        return mid

    def list_conversations(self, q: str = "", limit: int = 100) -> list[dict]:
        sql = """SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS n_messages,
                 (SELECT MAX(f.rating) FROM feedback f JOIN messages m ON m.id=f.message_id WHERE m.conversation_id=c.id) AS best_rating,
                 (SELECT MIN(f.rating) FROM feedback f JOIN messages m ON m.id=f.message_id WHERE m.conversation_id=c.id) AS worst_rating,
                 (SELECT t.id FROM tickets t WHERE t.conversation_id=c.id LIMIT 1) AS ticket_id
                 FROM conversations c"""
        args: tuple = ()
        if q:
            sql += " WHERE c.title LIKE ?"
            args = (f"%{q}%",)
        return self._all(sql + " ORDER BY c.updated_at DESC LIMIT ?", args + (limit,))

    def get_conversation(self, cid: str) -> dict | None:
        conv = self._all("SELECT * FROM conversations WHERE id=?", (cid,))
        if not conv:
            return None
        msgs = self._all("""SELECT m.*, (SELECT rating FROM feedback f WHERE f.message_id=m.id ORDER BY f.id DESC LIMIT 1) AS rating
                            FROM messages m WHERE conversation_id=? ORDER BY created_at""", (cid,))
        for m in msgs:
            for k in ("citations", "retrieved", "meta"):
                m[k] = json.loads(m[k] or "null")
        out = conv[0]
        out["messages"] = msgs
        out["tickets"] = self._all("SELECT id, status FROM tickets WHERE conversation_id=?", (cid,))
        return out

    def delete_conversation(self, cid: str) -> None:
        self._exec("DELETE FROM messages WHERE conversation_id=?", (cid,))
        self._exec("DELETE FROM conversations WHERE id=?", (cid,))

    def get_message(self, mid: str) -> dict | None:
        rows = self._all("SELECT * FROM messages WHERE id=?", (mid,))
        return rows[0] if rows else None

    # feedback
    def add_feedback(self, mid: str, rating: int, comment: str = "") -> None:
        self._exec("INSERT INTO feedback (message_id, rating, comment, created_at) VALUES (?,?,?,?)",
                   (mid, rating, comment[:1000], time.time()))

    def recent_feedback(self, limit: int = 20) -> list[dict]:
        return self._all("""SELECT f.*, m.content AS answer, c.title AS question, c.id AS conversation_id
                            FROM feedback f JOIN messages m ON m.id=f.message_id
                            JOIN conversations c ON c.id=m.conversation_id
                            ORDER BY f.id DESC LIMIT ?""", (limit,))

    # tickets
    def create_ticket(self, **t) -> int:
        now = time.time()
        cur = self._exec("""INSERT INTO tickets (conversation_id, message_id, subject, question, bot_answer, requester,
                            priority, status, assignee, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (t.get("conversation_id"), t.get("message_id"), t["subject"][:200], t.get("question", ""),
                          t.get("bot_answer", ""), t.get("requester", ""), t.get("priority", "normal"), "open",
                          t.get("assignee", ""), json.dumps([]), now, now))
        return cur.lastrowid

    def list_tickets(self, status: str = "") -> list[dict]:
        if status:
            return self._all("SELECT * FROM tickets WHERE status=? ORDER BY id DESC", (status,))
        return self._all("SELECT * FROM tickets ORDER BY id DESC")

    def get_ticket(self, tid: int) -> dict | None:
        rows = self._all("SELECT * FROM tickets WHERE id=?", (tid,))
        if not rows:
            return None
        rows[0]["notes"] = json.loads(rows[0]["notes"] or "[]")
        return rows[0]

    def update_ticket(self, tid: int, status: str | None = None, assignee: str | None = None,
                      priority: str | None = None, note: str | None = None) -> dict | None:
        t = self.get_ticket(tid)
        if not t:
            return None
        notes = t["notes"]
        if note:
            notes.append({"text": note[:2000], "at": time.time()})
        self._exec("UPDATE tickets SET status=?, assignee=?, priority=?, notes=?, updated_at=? WHERE id=?",
                   (status or t["status"], assignee if assignee is not None else t["assignee"],
                    priority or t["priority"], json.dumps(notes), time.time(), tid))
        return self.get_ticket(tid)

    # request metrics
    def log_request(self, path: str, status: int, latency_ms: float, **extra) -> None:
        self._exec("""INSERT INTO requests (path, status, latency_ms, ttft_ms, tokens_per_s, generator, top_score, created_at)
                      VALUES (?,?,?,?,?,?,?,?)""",
                   (path, status, latency_ms, extra.get("ttft_ms"), extra.get("tokens_per_s"),
                    extra.get("generator"), extra.get("top_score"), time.time()))

    def requests(self, path: str, since: float = 0) -> list[dict]:
        return self._all("SELECT * FROM requests WHERE path=? AND created_at>=? ORDER BY id", (path, since))

    # documents
    def upsert_document(self, source: str, kind: str, size: int, chunks: int, status: str, error: str = "") -> None:
        self._exec("INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?)",
                   (source, kind, size, chunks, status, error, time.time()))

    def documents(self) -> list[dict]:
        return self._all("SELECT * FROM documents ORDER BY ingested_at DESC, source")

    def delete_document(self, source: str) -> None:
        self._exec("DELETE FROM documents WHERE source=?", (source,))

    def counts(self) -> dict:
        one = lambda sql: self._all(sql)[0]["n"]
        return {"conversations": one("SELECT COUNT(*) n FROM conversations"),
                "questions": one("SELECT COUNT(*) n FROM messages WHERE role='user'"),
                "thumbs_up": one("SELECT COUNT(*) n FROM feedback WHERE rating>0"),
                "thumbs_down": one("SELECT COUNT(*) n FROM feedback WHERE rating<0"),
                "tickets_open": one("SELECT COUNT(*) n FROM tickets WHERE status!='resolved'"),
                "tickets_total": one("SELECT COUNT(*) n FROM tickets")}
