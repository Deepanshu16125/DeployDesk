"""Pluggable answer generators. All return {answer, citations, generator}."""
from __future__ import annotations
import os
import re

import httpx

from .ingest import Chunk
from .retriever import tokenize


def _cite(chunks: list[Chunk]) -> list[dict]:
    return [{"ref": i + 1, "source": c.source, "location": c.location, "chunk_id": c.id} for i, c in enumerate(chunks)]


class ExtractiveGenerator:
    """No-LLM fallback: pick the sentences that best overlap the question, with [n] citations."""
    name = "extractive"

    def answer(self, question: str, chunks: list[Chunk], max_sentences: int = 3) -> dict:
        q = set(tokenize(question))
        scored = []
        for ci, c in enumerate(chunks):
            for s in re.split(r"(?<=[.!?])\s+|\n{2,}", c.text):
                s = " ".join(s.split())
                if len(s) < 30:
                    continue
                overlap = len(q & set(tokenize(s)))
                scored.append((overlap - 0.15 * ci, ci, s))
        scored.sort(key=lambda x: -x[0])
        picked = [x for x in scored[:max_sentences] if x[0] > 0]
        if not picked:
            return {"answer": "I could not find this in the provided documents.", "citations": [], "generator": self.name}
        used = sorted({ci for _, ci, _ in picked})
        text = " ".join(f"{s} [{ci + 1}]" for _, ci, s in picked)
        return {"answer": text, "citations": [x for x in _cite(chunks) if x["ref"] - 1 in used], "generator": self.name}


PROMPT = """You are {assistant}, a support assistant for {customer}. Answer the question using ONLY the numbered context.
Cite sources inline like [1] or [2]. If the context does not contain the answer, say you don't know.

Context:
{context}

Question: {question}
Answer:"""


def _context(chunks: list[Chunk]) -> str:
    return "\n\n".join(f"[{i + 1}] ({c.source}, {c.location})\n{c.text}" for i, c in enumerate(chunks))


def _cited_refs(text: str, n: int) -> list[int]:
    return sorted({int(m) for m in re.findall(r"\[(\d+)\]", text) if 1 <= int(m) <= n})


class OllamaGenerator:
    name = "ollama"

    def __init__(self, url: str, model: str, assistant: str = "DeployDesk", customer: str = "the customer"):
        self.url, self.model, self.assistant, self.customer = url.rstrip("/"), model, assistant, customer

    def answer(self, question: str, chunks: list[Chunk]) -> dict:
        prompt = PROMPT.format(assistant=self.assistant, customer=self.customer, context=_context(chunks), question=question)
        r = httpx.post(f"{self.url}/api/generate", json={"model": self.model, "prompt": prompt, "stream": False,
                                                         "options": {"temperature": 0, "num_ctx": 4096}}, timeout=180)
        r.raise_for_status()
        text = r.json()["response"].strip()
        refs = _cited_refs(text, len(chunks))
        return {"answer": text, "citations": [c for c in _cite(chunks) if c["ref"] in refs], "generator": f"ollama:{self.model}"}


class GeminiGenerator:
    """Stub for Google Gemini / Vertex AI. Set GEMINI_API_KEY (or wire up Vertex AI credentials) to enable."""
    name = "gemini"

    def __init__(self, model: str, assistant: str = "DeployDesk", customer: str = "the customer"):
        self.model, self.assistant, self.customer = model, assistant, customer
        self.key = os.environ.get("GEMINI_API_KEY")

    def answer(self, question: str, chunks: list[Chunk]) -> dict:
        if not self.key:
            raise RuntimeError("GEMINI_API_KEY not set; Gemini/Vertex AI generator is a stub in this build")
        prompt = PROMPT.format(assistant=self.assistant, customer=self.customer, context=_context(chunks), question=question)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.key}"
        r = httpx.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=60)
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        refs = _cited_refs(text, len(chunks))
        return {"answer": text, "citations": [c for c in _cite(chunks) if c["ref"] in refs], "generator": f"gemini:{self.model}"}


def make_generator(settings) -> object:
    if settings.generator == "ollama":
        return OllamaGenerator(settings.ollama_url, settings.ollama_model, settings.assistant_name, settings.customer_name)
    if settings.generator == "gemini":
        return GeminiGenerator(settings.gemini_model, settings.assistant_name, settings.customer_name)
    return ExtractiveGenerator()


def answer_with_fallback(gen, question: str, chunks: list[Chunk]) -> dict:
    try:
        return gen.answer(question, chunks)
    except Exception as e:  # LLM down / no key -> never fail the customer request
        out = ExtractiveGenerator().answer(question, chunks)
        out["fallback_reason"] = f"{type(e).__name__}: {e}"[:200]
        return out
