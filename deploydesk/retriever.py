from __future__ import annotations

import json
import re
from pathlib import Path

import faiss
import numpy as np
from rank_bm25 import BM25Okapi

from .ingest import Chunk

_TOKEN = re.compile(r"[a-z0-9_]+")
_STOP = set("a an the of to in on for and or is are be by with what which how do does i you it as at from "
            "that this can when where why should my me we our".split())


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


class Index:
    def __init__(self, embedding_model: str, reranker_model: str | None = None):
        self.embedding_model_name = embedding_model
        self.reranker_model_name = reranker_model
        self._model = None
        self._reranker = None
        self.chunks: list[Chunk] = []
        self.vectors = np.zeros((0, 384), dtype="float32")
        self.faiss: faiss.Index | None = None
        self.bm25: BM25Okapi | None = None

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.embedding_model_name, device="cpu")
        return self._model

    @property
    def reranker(self):
        if self._reranker is None and self.reranker_model_name:
            from sentence_transformers import CrossEncoder
            self._reranker = CrossEncoder(self.reranker_model_name, device="cpu")
        return self._reranker

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = self.model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vecs, dtype="float32")

    def build(self, chunks: list[Chunk]) -> None:
        self.chunks = []
        self.vectors = np.zeros((0, self.model.get_embedding_dimension() if hasattr(self.model, "get_embedding_dimension") else self.model.get_sentence_embedding_dimension()), dtype="float32")
        self.add(chunks)

    def add(self, chunks: list[Chunk]) -> None:
        if chunks:
            new = self.embed([f"{c.location}\n{c.text}" for c in chunks])
            self.vectors = np.vstack([self.vectors, new]) if len(self.vectors) else new
            self.chunks.extend(chunks)
        self._rebuild()

    def remove_source(self, source: str) -> int:
        keep = [i for i, c in enumerate(self.chunks) if c.source != source]
        removed = len(self.chunks) - len(keep)
        self.chunks = [self.chunks[i] for i in keep]
        self.vectors = self.vectors[keep]
        self._rebuild()
        return removed

    def _rebuild(self) -> None:
        # flat index is fine at this size (~1.5k vectors, <1 ms per query); swap for HNSW past ~100k
        dim = self.vectors.shape[1] if len(self.vectors) else 384
        self.faiss = faiss.IndexFlatIP(dim)
        if len(self.vectors):
            self.faiss.add(self.vectors)
        corpus = [tokenize(f"{c.source} {c.location} {c.text}") for c in self.chunks]
        self.bm25 = BM25Okapi(corpus) if corpus else None

    def save(self, index_dir: str | Path) -> None:
        d = Path(index_dir)
        d.mkdir(parents=True, exist_ok=True)
        np.save(d / "vectors.npy", self.vectors)
        (d / "chunks.jsonl").write_text("\n".join(json.dumps(c.to_dict()) for c in self.chunks))
        (d / "meta.json").write_text(json.dumps({"embedding_model": self.embedding_model_name,
                                                 "chunks": len(self.chunks)}))

    @classmethod
    def load(cls, index_dir: str | Path, embedding_model: str, reranker_model: str | None = None) -> "Index":
        d = Path(index_dir)
        idx = cls(embedding_model, reranker_model)
        lines = (d / "chunks.jsonl").read_text().splitlines()
        idx.chunks = [Chunk(**json.loads(line)) for line in lines if line]
        if (d / "vectors.npy").exists():
            idx.vectors = np.load(d / "vectors.npy")
        else:  # index written by v0.1, re-embed once
            idx.vectors = idx.embed([f"{c.location}\n{c.text}" for c in idx.chunks])
        idx._rebuild()
        return idx

    def vector_search(self, query: str, k: int) -> list[tuple[int, float]]:
        if not self.chunks:
            return []
        scores, ids = self.faiss.search(self.embed([query]), min(k, len(self.chunks)))
        return [(int(i), float(s)) for i, s in zip(ids[0], scores[0]) if i >= 0]

    def bm25_search(self, query: str, k: int) -> list[tuple[int, float]]:
        if not self.bm25:
            return []
        scores = self.bm25.get_scores(tokenize(query))
        top = np.argsort(-scores)[:k]
        return [(int(i), float(scores[i])) for i in top if scores[i] > 0]

    def search(self, query: str, k: int = 5, mode: str = "hybrid", rrf_k: int = 60,
               pool: int = 30, rerank: bool = False, rerank_pool: int = 20) -> list[tuple[Chunk, float]]:
        n = rerank_pool if rerank else k
        if mode == "vector":
            hits = self.vector_search(query, n)
        elif mode == "bm25":
            hits = self.bm25_search(query, n)
        else:
            # reciprocal rank fusion; scores from the two retrievers aren't comparable so only ranks are used
            fused: dict[int, float] = {}
            for ranked in (self.vector_search(query, pool), self.bm25_search(query, pool)):
                for rank, (i, _) in enumerate(ranked):
                    fused[i] = fused.get(i, 0.0) + 1.0 / (rrf_k + rank + 1)
            hits = sorted(fused.items(), key=lambda x: -x[1])[:n]
        if rerank and hits and self.reranker is not None:
            pairs = [(query, self.chunks[i].text) for i, _ in hits]
            scores = self.reranker.predict(pairs, batch_size=32, show_progress_bar=False)
            hits = sorted(zip([i for i, _ in hits], map(float, scores)), key=lambda x: -x[1])
        return [(self.chunks[i], s) for i, s in hits[:k]]

    def relevance(self, query: str, chunks: list[Chunk]) -> float | None:
        """Best cross-encoder score among the chunks. Used as a 'did we find anything' signal."""
        if not chunks or self.reranker is None:
            return None
        scores = self.reranker.predict([(query, c.text) for c in chunks], show_progress_bar=False)
        return float(max(scores))
