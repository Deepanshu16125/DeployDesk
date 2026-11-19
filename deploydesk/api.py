"""FastAPI service: /health, /ingest, /ask, /metrics with API-key auth and JSON request logs."""
from __future__ import annotations
import json
import logging
import statistics
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .config import load_settings
from .generator import answer_with_fallback, make_generator
from .ingest import ingest_dir
from .retriever import Index

settings = load_settings()
log = logging.getLogger("deploydesk")
logging.basicConfig(level=logging.INFO, format="%(message)s")

app = FastAPI(title=f"{settings.assistant_name} for {settings.customer_name}", version=__version__)
_state: dict = {"index": None, "generator": make_generator(settings)}
_latencies: dict[str, deque] = {"/ask": deque(maxlen=1000), "/ingest": deque(maxlen=100)}
_lock = threading.Lock()
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def get_index() -> Index:
    if _state["index"] is None:
        if (Path(settings.index_dir) / "chunks.jsonl").exists():
            _state["index"] = Index.load(settings.index_dir, settings.embedding_model)
    return _state["index"]


def require_key(x_api_key: str | None = Header(default=None)):
    if x_api_key not in settings.api_keys:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


@app.middleware("http")
async def request_log(request: Request, call_next):
    rid = request.headers.get("x-request-id", uuid.uuid4().hex[:12])
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000
    if request.url.path in _latencies and response.status_code == 200:
        _latencies[request.url.path].append(ms)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "request_id": rid, "method": request.method,
           "path": request.url.path, "status": response.status_code, "latency_ms": round(ms, 1)}
    log.info(json.dumps(rec))
    try:
        Path(settings.log_file).parent.mkdir(parents=True, exist_ok=True)
        with open(settings.log_file, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass
    response.headers["X-Request-ID"] = rid
    response.headers["X-Latency-Ms"] = f"{ms:.1f}"
    return response


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    mode: str | None = Field(default=None, pattern="^(hybrid|vector|bm25)$")


class IngestRequest(BaseModel):
    docs_dir: str | None = None


@app.get("/health")
def health():
    idx = get_index()
    return {"status": "ok" if idx else "no_index", "version": __version__, "customer": settings.customer_name,
            "chunks": len(idx.chunks) if idx else 0, "generator": settings.generator,
            "retrieval_mode": settings.retrieval_mode}


@app.post("/ingest", dependencies=[Depends(require_key)])
def ingest(req: IngestRequest):
    t0 = time.perf_counter()
    chunks, n_files = ingest_dir(req.docs_dir or settings.docs_dir, settings.chunk_size, settings.chunk_overlap)
    if not chunks:
        raise HTTPException(400, "no supported documents found")
    idx = Index(settings.embedding_model)
    idx.build(chunks)
    idx.save(settings.index_dir)
    with _lock:
        _state["index"] = idx
    return {"documents": n_files, "chunks": len(chunks), "seconds": round(time.perf_counter() - t0, 2)}


@app.post("/ask", dependencies=[Depends(require_key)])
def ask(req: AskRequest):
    idx = get_index()
    if idx is None:
        raise HTTPException(409, "no index yet; call /ingest first")
    t0 = time.perf_counter()
    hits = idx.search(req.question, k=req.top_k or settings.top_k, mode=req.mode or settings.retrieval_mode, rrf_k=settings.rrf_k)
    t_ret = (time.perf_counter() - t0) * 1000
    chunks = [c for c, _ in hits]
    out = answer_with_fallback(_state["generator"], req.question, chunks)
    t_total = (time.perf_counter() - t0) * 1000
    return {"question": req.question, **out,
            "retrieved": [{"ref": i + 1, "source": c.source, "location": c.location, "score": round(s, 4),
                           "snippet": c.text[:240]} for i, (c, s) in enumerate(hits)],
            "timing_ms": {"retrieval": round(t_ret, 1), "total": round(t_total, 1)}}


def _pct(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    return round(s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))], 1)


@app.get("/metrics", dependencies=[Depends(require_key)])
def metrics():
    return {path: {"count": len(v), "p50_ms": _pct(v, 50), "p95_ms": _pct(v, 95),
                   "mean_ms": round(statistics.mean(v), 1) if v else None} for path, v in _latencies.items()}


@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(STATIC / "index.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(STATIC / "dashboard.html")


@app.get("/stats")
def stats():
    # no auth on purpose: counts and eval scores only, nothing from the documents
    idx = get_index()
    eval_file = Path("eval/results.json")
    return {"customer": settings.customer_name, "documents": len({c.source for c in idx.chunks}) if idx else 0,
            "chunks": len(idx.chunks) if idx else 0, "generator": settings.generator,
            "retrieval_mode": settings.retrieval_mode,
            "eval": json.loads(eval_file.read_text()) if eval_file.exists() else None,
            "live": metrics()}
