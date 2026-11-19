"""Vector (FAISS + sentence-transformers), BM25 and hybrid (RRF) retrieval."""
from __future__ import annotations
import json
import re
from pathlib import Path

import faiss
import numpy as np
from rank_bm25 import BM25Okapi

from .ingest import Chunk

_TOKEN = re.compile(r"[a-z0-9_]+")
_STOP = set("a an the of to in on for and or is are be by with what which how do does i you it as at from that this can when where why should".split())


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


class Index:
    def __init__(self, embedding_model: str):
        self.embedding_model_name = embedding_model
        self._model = None
        self.chunks: list[Chunk] = []
        self.faiss: faiss.Index | None = None
        self.bm25: BM25Okapi | None = None

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.embedding_model_name, device="cpu")
        return self._model

    def embed(self, texts: list[str]) -> np.ndarray:
        v = self.model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(v, dtype="float32")

    def build(self, chunks: list[Chunk]):
        self.chunks = chunks
        vecs = self.embed([f"{c.location}\n{c.text}" for c in chunks])
        self.faiss = faiss.IndexFlatIP(vecs.shape[1])
        self.faiss.add(vecs)
        self._build_bm25()

    def _build_bm25(self):
        self.bm25 = BM25Okapi([tokenize(f"{c.source} {c.location} {c.text}") for c in self.chunks])

    def save(self, index_dir: str | Path):
        d = Path(index_dir)
        d.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.faiss, str(d / "vectors.faiss"))
        (d / "chunks.jsonl").write_text("\n".join(json.dumps(c.to_dict()) for c in self.chunks))
        (d / "meta.json").write_text(json.dumps({"embedding_model": self.embedding_model_name, "chunks": len(self.chunks)}))

    @classmethod
    def load(cls, index_dir: str | Path, embedding_model: str) -> "Index":
        d = Path(index_dir)
        idx = cls(embedding_model)
        idx.chunks = [Chunk(**json.loads(l)) for l in (d / "chunks.jsonl").read_text().splitlines() if l]
        idx.faiss = faiss.read_index(str(d / "vectors.faiss"))
        idx._build_bm25()
        return idx

    # --- retrieval ---------------------------------------------------------
    def vector_search(self, query: str, k: int) -> list[tuple[int, float]]:
        q = self.embed([query])
        scores, ids = self.faiss.search(q, k)
        return [(int(i), float(s)) for i, s in zip(ids[0], scores[0]) if i >= 0]

    def bm25_search(self, query: str, k: int) -> list[tuple[int, float]]:
        scores = self.bm25.get_scores(tokenize(query))
        top = np.argsort(-scores)[:k]
        return [(int(i), float(scores[i])) for i in top if scores[i] > 0]

    def search(self, query: str, k: int = 5, mode: str = "hybrid", rrf_k: int = 60, pool: int = 30):
        if mode == "vector":
            hits = self.vector_search(query, k)
        elif mode == "bm25":
            hits = self.bm25_search(query, k)
        else:  # reciprocal rank fusion of both lists
            fused: dict[int, float] = {}
            for ranked in (self.vector_search(query, pool), self.bm25_search(query, pool)):
                for rank, (i, _) in enumerate(ranked):
                    fused[i] = fused.get(i, 0.0) + 1.0 / (rrf_k + rank + 1)
            hits = sorted(fused.items(), key=lambda x: -x[1])[:k]
        return [(self.chunks[i], s) for i, s in hits]
