from __future__ import annotations

import json
import logging
import math
import re
import shutil
import statistics
import threading
import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .config import EDITABLE, load_settings, save_settings
from .generator import (NOT_FOUND, ExtractiveGenerator, answer_with_fallback, citations_for, cited_refs,
                        make_generator, normalize_citations)
from .highlight import highlights
from .ingest import SUPPORTED, ingest_dir, ingest_file, list_files
from .retriever import Index
from .store import Store

settings = load_settings()
log = logging.getLogger("deploydesk")
logging.basicConfig(level=logging.INFO, format="%(message)s")

app = FastAPI(title=f"{settings.assistant_name} API", version=__version__,
              description=f"Support assistant for {settings.customer_name}. Auth: X-API-Key header.")
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")

store = Store(settings.db_path)
_state: dict = {"index": None, "generator": make_generator(settings)}
_index_lock = threading.Lock()


def get_index() -> Index | None:
    if _state["index"] is None and (Path(settings.index_dir) / "chunks.jsonl").exists():
        with _index_lock:
            if _state["index"] is None:
                _state["index"] = Index.load(settings.index_dir, settings.embedding_model, settings.reranker_model)
    return _state["index"]


@app.on_event("startup")
def warm_up() -> None:
    # load the embedder + cross-encoder now, otherwise the first customer question pays ~5 s for it
    idx = get_index()
    if idx and idx.chunks:
        idx.search("warm up", 1, rerank=settings.rerank)


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    if x_api_key not in settings.api_keys:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


@app.middleware("http")
async def request_log(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000
    path = request.url.path
    if path.startswith("/static") or path in ("/favicon.ico",):
        return response
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "request_id": rid, "method": request.method,
           "path": path, "status": response.status_code, "latency_ms": round(ms, 1)}
    log.info(json.dumps(rec))
    try:
        Path(settings.log_file).parent.mkdir(parents=True, exist_ok=True)
        with open(settings.log_file, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass
    if path == "/ask":  # streaming endpoint logs itself once the stream finishes
        store.log_request(path, response.status_code, ms)
    response.headers["X-Request-ID"] = rid
    response.headers["X-Latency-Ms"] = f"{ms:.1f}"
    return response


# pages

@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    idx = get_index()
    return {"status": "ok" if idx and idx.chunks else "no_index", "version": __version__,
            "customer": settings.customer_name, "chunks": len(idx.chunks) if idx else 0,
            "generator": settings.generator, "model": settings.ollama_model if settings.generator == "ollama" else None,
            "retrieval_mode": settings.retrieval_mode, "rerank": settings.rerank}


@app.get("/api/config")
def public_config():
    out = {k: getattr(settings, k) for k in ("customer_name", "assistant_name", "greeting", "suggested_questions",
                                              "generator", "ollama_model", "retrieval_mode", "rerank")}
    out["version"] = __version__
    if settings.demo_mode:  # lets the demo UI work without typing a key; off by default
        out["demo_key"] = settings.api_keys[0]
    return out


# ask

class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    mode: str | None = Field(default=None, pattern="^(hybrid|vector|bm25)$")
    rerank: bool | None = None
    conversation_id: str | None = None


def _retrieve(req: AskRequest, idx: Index, query: str | None = None):
    t0 = time.perf_counter()
    rerank = settings.rerank if req.rerank is None else req.rerank
    query = query or req.question
    hits = idx.search(query, k=req.top_k or settings.top_k, mode=req.mode or settings.retrieval_mode,
                      rrf_k=settings.rrf_k, rerank=rerank)
    if rerank:
        score = hits[0][1] if hits else None
    else:
        # ordering stays hybrid (it won on hit@1), but the cross-encoder still decides if anything relevant came back
        score = idx.relevance(query, [c for c, _ in hits[: settings.context_k]])
    return hits, score, (time.perf_counter() - t0) * 1000


def _confidence(score: float | None) -> tuple[float | None, str]:
    if score is None:
        return None, "unknown"
    if score < settings.min_rerank_score:
        return round(score, 2), "none"
    return round(score, 2), "high" if score > 3 else "medium" if score > -2.5 else "low"


def _retrieved_payload(hits, full: bool = False) -> list[dict]:
    return [{"ref": i + 1, "source": c.source, "location": c.location, "score": round(s, 3), "chunk_id": c.id,
             **({"text": c.text} if full else {"snippet": c.text[:240]})} for i, (c, s) in enumerate(hits)]


@app.post("/ask", dependencies=[Depends(require_key)])
def ask(req: AskRequest):
    """Plain JSON answer (no streaming, not saved to history). Handy for scripts and integrations."""
    idx = get_index()
    if idx is None or not idx.chunks:
        raise HTTPException(409, "no index yet; call /ingest first")
    t0 = time.perf_counter()
    hits, raw_score, t_ret = _retrieve(req, idx)
    score, conf = _confidence(raw_score)
    chunks = [c for c, _ in hits][: settings.context_k]
    if conf == "none":
        out = {"answer": NOT_FOUND, "citations": [], "generator": "guardrail"}
    else:
        out = answer_with_fallback(_state["generator"], req.question, chunks)
    return {"question": req.question, **out, "confidence": conf, "top_score": score,
            "retrieved": _retrieved_payload(hits),
            "timing_ms": {"retrieval": round(t_ret, 1), "total": round((time.perf_counter() - t0) * 1000, 1)}}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/api/ask/stream", dependencies=[Depends(require_key)])
def ask_stream(req: AskRequest):
    idx = get_index()
    if idx is None or not idx.chunks:
        raise HTTPException(409, "the knowledge base is empty; upload documents first")
    cid = req.conversation_id
    previous = ""
    if cid and store.conversation_exists(cid):
        users = [m["content"] for m in store.get_conversation(cid)["messages"] if m["role"] == "user"]
        previous = users[-1] if users else ""
    else:
        cid = store.new_conversation(req.question)
    store.add_message(cid, "user", req.question)
    # follow-ups like "and in production?" retrieve nothing useful on their own, so carry the last question along
    query = f"{previous} {req.question}" if previous else req.question
    llm_question = f"{req.question}\n(Earlier in this conversation they asked: {previous})" if previous else req.question

    def events():
        t0 = time.perf_counter()
        hits, raw_score, t_ret = _retrieve(req, idx, query)
        score, conf = _confidence(raw_score)
        chunks = [c for c, _ in hits][: settings.context_k]
        yield _sse("meta", {"conversation_id": cid, "retrieved": _retrieved_payload(hits[: settings.context_k], full=True),
                            "confidence": conf, "top_score": score, "retrieval_ms": round(t_ret, 1)})
        gen = _state["generator"]
        gen_name = f"{gen.name}:{gen.model}" if hasattr(gen, "model") else gen.name
        parts: list[str] = []
        stats: dict = {}
        fallback = None
        if conf == "none":
            parts, gen_name = [NOT_FOUND], "guardrail"
            yield _sse("token", {"t": NOT_FOUND})
        else:
            try:
                for tok in gen.stream(llm_question, chunks):
                    parts.append(tok)
                    yield _sse("token", {"t": tok})
                stats = getattr(gen, "last_stats", {}) or {}
            except Exception as e:  # model down mid-request: finish with the extractive answer
                fallback = f"{type(e).__name__}: {e}"[:200]
                text = ExtractiveGenerator().answer(query, chunks)["answer"]
                parts, gen_name = [text], "extractive"
                yield _sse("replace", {"text": text, "reason": fallback})
        answer = normalize_citations("".join(parts).strip())
        refs = cited_refs(answer, len(chunks))
        total = (time.perf_counter() - t0) * 1000
        meta = {"generator": gen_name, "confidence": conf, "top_score": score, "retrieval_ms": round(t_ret, 1),
                "total_ms": round(total, 1), **stats, **({"fallback_reason": fallback} if fallback else {})}
        citations = citations_for(chunks, refs)
        mid = store.add_message(cid, "assistant", answer, citations, _retrieved_payload(hits[: settings.context_k]), meta)
        store.log_request("/api/ask/stream", 200, total, ttft_ms=stats.get("ttft_ms"),
                          tokens_per_s=stats.get("tokens_per_s"), generator=gen_name, top_score=score)
        yield _sse("done", {"message_id": mid, "conversation_id": cid, "answer": answer, "citations": citations,
                            "highlights": highlights(answer, chunks, refs), **meta})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# conversations / feedback / tickets

@app.get("/api/conversations", dependencies=[Depends(require_key)])
def conversations(q: str = ""):
    return store.list_conversations(q)


@app.get("/api/conversations/{cid}", dependencies=[Depends(require_key)])
def conversation(cid: str):
    conv = store.get_conversation(cid)
    if not conv:
        raise HTTPException(404, "conversation not found")
    return conv


@app.delete("/api/conversations/{cid}", dependencies=[Depends(require_key)])
def delete_conversation(cid: str):
    store.delete_conversation(cid)
    return {"deleted": cid}


class FeedbackRequest(BaseModel):
    message_id: str
    rating: int = Field(ge=-1, le=1)
    comment: str = Field(default="", max_length=1000)


@app.post("/api/feedback", dependencies=[Depends(require_key)])
def feedback(req: FeedbackRequest):
    if not store.get_message(req.message_id):
        raise HTTPException(404, "message not found")
    store.add_feedback(req.message_id, req.rating, req.comment)
    return {"ok": True}


class TicketRequest(BaseModel):
    conversation_id: str | None = None
    message_id: str | None = None
    subject: str = Field(min_length=3, max_length=200)
    requester: str = Field(default="", max_length=200)
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")
    details: str = Field(default="", max_length=4000)


@app.post("/api/tickets", dependencies=[Depends(require_key)])
def create_ticket(req: TicketRequest):
    question, answer = req.details, ""
    if req.message_id and (msg := store.get_message(req.message_id)):
        answer = msg["content"]
        conv = store.get_conversation(msg["conversation_id"]) or {}
        users = [m["content"] for m in conv.get("messages", []) if m["role"] == "user" and m["created_at"] <= msg["created_at"]]
        question = (users[-1] if users else "") + (f"\n\n{req.details}" if req.details else "")
    tid = store.create_ticket(conversation_id=req.conversation_id, message_id=req.message_id, subject=req.subject,
                              question=question, bot_answer=answer, requester=req.requester, priority=req.priority)
    return store.get_ticket(tid)


@app.get("/api/tickets", dependencies=[Depends(require_key)])
def tickets(status: str = ""):
    return store.list_tickets(status)


class TicketUpdate(BaseModel):
    status: str | None = Field(default=None, pattern="^(open|in_progress|waiting|resolved)$")
    assignee: str | None = Field(default=None, max_length=100)
    priority: str | None = Field(default=None, pattern="^(low|normal|high|urgent)$")
    note: str | None = Field(default=None, max_length=2000)


@app.get("/api/tickets/{tid}", dependencies=[Depends(require_key)])
def ticket(tid: int):
    t = store.get_ticket(tid)
    if not t:
        raise HTTPException(404, "ticket not found")
    return t


@app.patch("/api/tickets/{tid}", dependencies=[Depends(require_key)])
def update_ticket(tid: int, req: TicketUpdate):
    t = store.update_ticket(tid, req.status, req.assignee, req.priority, req.note)
    if not t:
        raise HTTPException(404, "ticket not found")
    return t


# knowledge base

def _kind(name: str) -> str:
    ext = Path(name).suffix.lower().lstrip(".")
    return {"htm": "html", "markdown": "md"}.get(ext, ext)


def _sync_documents(idx: Index) -> None:
    counts: dict[str, int] = {}
    for c in idx.chunks:
        counts[c.source] = counts.get(c.source, 0) + 1
    known = {d["source"] for d in store.documents()}
    root = Path(settings.docs_dir)
    for src, n in counts.items():
        if src not in known:
            p = root / src
            store.upsert_document(src, _kind(src), p.stat().st_size if p.exists() else 0, n, "indexed")


@app.get("/api/documents", dependencies=[Depends(require_key)])
def documents():
    idx = get_index()
    if idx:
        _sync_documents(idx)
    return store.documents()


@app.get("/api/documents/chunks", dependencies=[Depends(require_key)])
def document_chunks(source: str):
    idx = get_index()
    chunks = [c.to_dict() for c in (idx.chunks if idx else []) if c.source == source]
    if not chunks:
        raise HTTPException(404, "document not in index")
    return chunks


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


@app.post("/api/documents", dependencies=[Depends(require_key)])
def upload_documents(files: list[UploadFile] = File(...)):
    idx = get_index()
    if idx is None:
        idx = Index(settings.embedding_model, settings.reranker_model)
        idx.build([])
        _state["index"] = idx
    upload_dir = Path(settings.docs_dir) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for f in files:
        name = _SAFE.sub("-", Path(f.filename or "file").name).strip("-") or "file"
        rel = f"uploads/{name}"
        if Path(name).suffix.lower() not in SUPPORTED:
            results.append({"source": rel, "status": "rejected", "error": "unsupported file type"})
            continue
        dest = upload_dir / name
        with dest.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        t0 = time.perf_counter()
        try:
            chunks = ingest_file(dest, rel, settings.chunk_size, settings.chunk_overlap)
            if not chunks:
                raise ValueError("no extractable text (scanned PDF?)")
            with _index_lock:
                idx.remove_source(rel)  # re-uploading the same file replaces it
                idx.add(chunks)
            store.upsert_document(rel, _kind(name), dest.stat().st_size, len(chunks), "indexed")
            results.append({"source": rel, "status": "indexed", "chunks": len(chunks),
                            "seconds": round(time.perf_counter() - t0, 2)})
        except Exception as e:
            store.upsert_document(rel, _kind(name), dest.stat().st_size, 0, "failed", str(e)[:300])
            results.append({"source": rel, "status": "failed", "error": str(e)[:300]})
    with _index_lock:
        idx.save(settings.index_dir)
    return {"results": results, "total_chunks": len(idx.chunks)}


@app.delete("/api/documents", dependencies=[Depends(require_key)])
def delete_document(source: str):
    idx = get_index()
    removed = 0
    if idx:
        with _index_lock:
            removed = idx.remove_source(source)
            idx.save(settings.index_dir)
    store.delete_document(source)
    path = (Path(settings.docs_dir) / source).resolve()
    if path.is_file() and Path(settings.docs_dir).resolve() in path.parents:
        path.unlink()
    return {"source": source, "chunks_removed": removed}


class IngestRequest(BaseModel):
    docs_dir: str | None = None


@app.post("/ingest", dependencies=[Depends(require_key)])
def ingest(req: IngestRequest):
    t0 = time.perf_counter()
    root = req.docs_dir or settings.docs_dir
    chunks, n_files = ingest_dir(root, settings.chunk_size, settings.chunk_overlap)
    if not chunks:
        raise HTTPException(400, "no supported documents found")
    idx = Index(settings.embedding_model, settings.reranker_model)
    idx.build(chunks)
    idx.save(settings.index_dir)
    with _index_lock:
        _state["index"] = idx
    counts: dict[str, int] = {}
    for c in chunks:
        counts[c.source] = counts.get(c.source, 0) + 1
    for f in list_files(root):
        rel = str(f.relative_to(root))
        store.upsert_document(rel, _kind(rel), f.stat().st_size, counts.get(rel, 0),
                              "indexed" if counts.get(rel) else "failed", "" if counts.get(rel) else "no text")
    return {"documents": n_files, "chunks": len(chunks), "seconds": round(time.perf_counter() - t0, 2)}


# analytics / settings

def _pct(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    return round(s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))], 1)


def _histogram(vals: list[float], bins: int = 12) -> list[dict]:
    if not vals:
        return []
    hi = _pct(vals, 98) or max(vals)
    width = max(hi / bins, 1e-9)
    counts = [0] * bins
    for v in vals:
        counts[min(bins - 1, int(v / width))] += 1
    return [{"from": round(i * width, 1), "to": round((i + 1) * width, 1), "count": n} for i, n in enumerate(counts)]


@app.get("/metrics", dependencies=[Depends(require_key)])
def metrics():
    out = {}
    for path in ("/api/ask/stream", "/ask"):
        lat = [r["latency_ms"] for r in store.requests(path)]
        out[path] = {"count": len(lat), "p50_ms": _pct(lat, 50), "p95_ms": _pct(lat, 95),
                     "mean_ms": round(statistics.mean(lat), 1) if lat else None}
    return out


@app.get("/api/analytics", dependencies=[Depends(require_key)])
def analytics():
    idx = get_index()
    reqs = store.requests("/api/ask/stream")
    llm = [r for r in reqs if (r["generator"] or "").startswith("ollama")]
    lat = [r["latency_ms"] / 1000 for r in llm] or [r["latency_ms"] / 1000 for r in reqs]
    ttft = [r["ttft_ms"] for r in llm if r["ttft_ms"]]
    tps = [r["tokens_per_s"] for r in llm if r["tokens_per_s"]]
    today = time.time()
    days = []
    for d in range(13, -1, -1):
        start = today - (d + 1) * 86400
        end = today - d * 86400
        days.append({"day": time.strftime("%d %b", time.localtime(end)),
                     "count": sum(1 for r in reqs if start <= r["created_at"] < end)})
    hours = []
    for h in range(23, -1, -1):
        end = today - h * 3600
        hours.append({"day": time.strftime("%H:00", time.localtime(end - 3600)),
                      "count": sum(1 for r in reqs if end - 3600 <= r["created_at"] < end)})
    span_days = (today - reqs[0]["created_at"]) / 86400 if reqs else 0
    counts: dict[str, int] = {}
    for m in store._all("SELECT citations FROM messages WHERE role='assistant'"):
        for c in json.loads(m["citations"] or "[]"):
            counts[c["source"]] = counts.get(c["source"], 0) + 1
    top_sources = sorted(counts.items(), key=lambda x: -x[1])[:6]
    unanswered = store._all("""SELECT c.title AS question, m.created_at, c.id AS conversation_id FROM messages m
                               JOIN conversations c ON c.id=m.conversation_id
                               WHERE m.role='assistant' AND m.content LIKE ? ORDER BY m.created_at DESC LIMIT 8""",
                            (f"%{NOT_FOUND[:30]}%",))
    evals = {}
    for name in ("results", "results_rerank", "results_llm"):
        p = Path("eval") / f"{name}.json"
        if p.exists():
            evals[name] = json.loads(p.read_text())
    return {"counts": {**store.counts(), "documents": len({c.source for c in idx.chunks}) if idx else 0,
                       "chunks": len(idx.chunks) if idx else 0},
            "latency": {"histogram_s": _histogram(lat), "p50_s": _pct(lat, 50), "p95_s": _pct(lat, 95),
                        "n": len(lat), "ttft_p50_ms": _pct(ttft, 50), "ttft_p95_ms": _pct(ttft, 95),
                        "tokens_per_s_p50": _pct(tps, 50)},
            "volume": days if span_days > 2 else hours, "volume_unit": "day" if span_days > 2 else "hour",
            "top_sources": [{"source": s, "count": n} for s, n in top_sources],
            "unanswered": unanswered, "feedback": store.recent_feedback(8), "eval": evals,
            "generator": settings.generator, "model": settings.ollama_model}


@app.get("/api/settings", dependencies=[Depends(require_key)])
def get_settings():
    return {**settings.public(), "editable": sorted(EDITABLE)}


@app.put("/api/settings", dependencies=[Depends(require_key)])
def put_settings(body: dict):
    bad = set(body) - EDITABLE
    if bad:
        raise HTTPException(400, f"not editable: {', '.join(sorted(bad))}")
    if "retrieval_mode" in body and body["retrieval_mode"] not in ("hybrid", "vector", "bm25"):
        raise HTTPException(400, "retrieval_mode must be hybrid, vector or bm25")
    if "generator" in body and body["generator"] not in ("extractive", "ollama", "gemini"):
        raise HTTPException(400, "generator must be extractive, ollama or gemini")
    for k, v in body.items():
        current = getattr(settings, k)
        try:
            v = type(current)(v) if not isinstance(current, list) else [str(x) for x in v if str(x).strip()]
        except (TypeError, ValueError):
            raise HTTPException(400, f"bad value for {k}")
        if isinstance(v, float) and math.isnan(v):
            raise HTTPException(400, f"bad value for {k}")
        setattr(settings, k, v)
    _state["generator"] = make_generator(settings)
    save_settings(settings)
    return get_settings()


@app.get("/api/llm/status", dependencies=[Depends(require_key)])
def llm_status():
    import httpx
    try:
        tags = httpx.get(f"{settings.ollama_url}/api/tags", timeout=2).json()
        models = [m["name"] for m in tags.get("models", [])]
        return {"reachable": True, "models": models}
    except Exception as e:
        return {"reachable": False, "error": str(e)[:200], "models": []}
