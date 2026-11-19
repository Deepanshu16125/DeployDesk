from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterator

import httpx

from .ingest import Chunk
from .retriever import tokenize

NOT_FOUND = "I couldn't find this in the knowledge base."

SYSTEM = """You are the support assistant for {customer}. Answer using ONLY the numbered sources the user gives you.
Rules:
- Put the source number in square brackets at the end of EVERY sentence, before the full stop. Example:
  "Use `CORSMiddleware` to allow other origins [2]. Pass the allowed URLs in `allow_origins` [2]."
- Be concise: 2 to 5 sentences, or a short list of steps. Include exact names (functions, flags, settings) from the sources.
- If the sources do not contain the answer, reply exactly: "{not_found}"
- Never invent sources, numbers, URLs or code that is not in the sources."""


def build_messages(question: str, chunks: list[Chunk], customer: str) -> list[dict]:
    context = "\n\n".join(f"[{i + 1}] {c.source} — {c.location}\n{c.text}" for i, c in enumerate(chunks))
    return [{"role": "system", "content": SYSTEM.format(customer=customer, not_found=NOT_FOUND)},
            {"role": "user", "content": f"Sources:\n\n{context}\n\nQuestion: {question}\n\n"
                                        "Answer in 2 to 5 sentences using only the sources above, staying close to their wording. "
                                        "End every sentence with its source number, like [1]. No code blocks."}]


def normalize_citations(text: str) -> str:
    # small models like to write [1, 2] or [1][2] or (1); the UI wants plain [1] [2]
    text = re.sub(r"\[(\d+(?:\s*,\s*\d+)+)\]", lambda m: " ".join(f"[{n.strip()}]" for n in m.group(1).split(",")), text)
    text = re.sub(r"\[Source (\d+)\]", r"[\1]", text, flags=re.I)
    # "sentence. [3]" -> "sentence [3]." so the citation stays attached to its sentence
    text = re.sub(r"([.!?])((?:\s*\[\d+\])+)", lambda m: m.group(2) + m.group(1), text)
    return re.sub(r"^\s*(\[\d+\]\s*)+", "", text)  # a citation before any text means nothing


def cited_refs(text: str, n: int) -> list[int]:
    return sorted({int(m) for m in re.findall(r"\[(\d+)\]", text) if 1 <= int(m) <= n})


def citations_for(chunks: list[Chunk], refs: list[int]) -> list[dict]:
    return [{"ref": r, "source": chunks[r - 1].source, "location": chunks[r - 1].location,
             "chunk_id": chunks[r - 1].id} for r in refs]


class ExtractiveGenerator:
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
            return {"answer": NOT_FOUND, "citations": [], "generator": self.name}
        text = " ".join(f"{s} [{ci + 1}]" for _, ci, s in picked)
        return {"answer": text, "citations": citations_for(chunks, cited_refs(text, len(chunks))),
                "generator": self.name}

    def stream(self, question: str, chunks: list[Chunk]) -> Iterator[str]:
        yield self.answer(question, chunks)["answer"]


class OllamaGenerator:
    name = "ollama"

    def __init__(self, url: str, model: str, customer: str = "the customer"):
        self.url, self.model, self.customer = url.rstrip("/"), model, customer
        self.last_stats: dict = {}

    def stream(self, question: str, chunks: list[Chunk]) -> Iterator[str]:
        payload = {"model": self.model, "messages": build_messages(question, chunks, self.customer),
                   "stream": True, "keep_alive": "30m",
                   "options": {"temperature": 0.1, "num_ctx": 4096, "num_predict": 320}}
        t0 = time.perf_counter()
        first = None
        with httpx.stream("POST", f"{self.url}/api/chat", json=payload, timeout=httpx.Timeout(180, connect=3)) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                token = data.get("message", {}).get("content", "")
                if token:
                    if first is None:
                        first = time.perf_counter() - t0
                    yield token
                if data.get("done"):
                    ev, ed = data.get("eval_count", 0), data.get("eval_duration", 0)
                    self.last_stats = {"ttft_ms": round((first or 0) * 1000, 1),
                                       "tokens": ev, "tokens_per_s": round(ev / ed * 1e9, 1) if ed else None,
                                       "prompt_tokens": data.get("prompt_eval_count")}

    def answer(self, question: str, chunks: list[Chunk]) -> dict:
        text = normalize_citations("".join(self.stream(question, chunks)).strip())
        return {"answer": text, "citations": citations_for(chunks, cited_refs(text, len(chunks))),
                "generator": f"ollama:{self.model}", "llm_stats": self.last_stats}


class GeminiGenerator:
    # stub: not tested against a real key yet
    name = "gemini"

    def __init__(self, model: str, customer: str = "the customer"):
        self.model, self.customer = model, customer
        self.key = os.environ.get("GEMINI_API_KEY")

    def answer(self, question: str, chunks: list[Chunk]) -> dict:
        if not self.key:
            raise RuntimeError("GEMINI_API_KEY not set; Gemini/Vertex AI generator is a stub in this build")
        msgs = build_messages(question, chunks, self.customer)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {"system_instruction": {"parts": [{"text": msgs[0]["content"]}]},
                "contents": [{"role": "user", "parts": [{"text": msgs[1]["content"]}]}]}
        r = httpx.post(url, params={"key": self.key}, json=body, timeout=60)
        r.raise_for_status()
        text = normalize_citations(r.json()["candidates"][0]["content"]["parts"][0]["text"])
        return {"answer": text, "citations": citations_for(chunks, cited_refs(text, len(chunks))),
                "generator": f"gemini:{self.model}"}

    def stream(self, question: str, chunks: list[Chunk]) -> Iterator[str]:
        yield self.answer(question, chunks)["answer"]


def make_generator(settings):
    if settings.generator == "ollama":
        return OllamaGenerator(settings.ollama_url, settings.ollama_model, settings.customer_name)
    if settings.generator == "gemini":
        return GeminiGenerator(settings.gemini_model, settings.customer_name)
    return ExtractiveGenerator()


def answer_with_fallback(gen, question: str, chunks: list[Chunk]) -> dict:
    try:
        return gen.answer(question, chunks)
    except Exception as e:  # LLM down / no key: degrade to extractive rather than 500 the customer
        out = ExtractiveGenerator().answer(question, chunks)
        out["fallback_reason"] = f"{type(e).__name__}: {e}"[:200]
        return out
