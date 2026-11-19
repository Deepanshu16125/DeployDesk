"""Retrieval + answer eval over eval/qa.jsonl.

A retrieved chunk counts as relevant if it comes from the labelled source file
AND contains the labelled key phrase. Doc-level labels alone were too lenient
for the two big NIST PDFs (any page would count).
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deploydesk.config import load_settings
from deploydesk.generator import ExtractiveGenerator, OllamaGenerator, answer_with_fallback
from deploydesk.retriever import Index


def is_relevant(chunk, row):
    return chunk.source == row["source"] and row["key"].lower() in chunk.text.lower()


def pct(vals, p):
    s = sorted(vals)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


def eval_mode(index, rows, mode, gen, k_values=(1, 3, 5, 10)):
    hits = {k: 0 for k in k_values}
    rr_total, cite_ok, answered, lat = 0.0, 0, 0, []
    for row in rows:
        t0 = time.perf_counter()
        results = index.search(row["q"], k=max(k_values), mode=mode)
        chunks = [c for c, _ in results]
        out = answer_with_fallback(gen, row["q"], chunks[:5]) if gen else None
        lat.append((time.perf_counter() - t0) * 1000)

        first = next((i for i, c in enumerate(chunks) if is_relevant(c, row)), None)
        for k in k_values:
            hits[k] += first is not None and first < k
        rr_total += 1.0 / (first + 1) if first is not None else 0.0
        if out is not None:
            cited = [chunks[c["ref"] - 1] for c in out["citations"]]
            if cited:
                answered += 1
                cite_ok += any(c.source == row["source"] for c in cited)
    n = len(rows)
    res = {f"hit@{k}": round(hits[k] / n, 3) for k in k_values}
    res["mrr@10"] = round(rr_total / n, 3)
    if gen:
        res["citation_accuracy"] = round(cite_ok / n, 3)
        res["answered_with_citation"] = answered
    res["latency_p50_ms"] = round(pct(lat, 50), 1)
    res["latency_p95_ms"] = round(pct(lat, 95), 1)
    res["latency_mean_ms"] = round(statistics.mean(lat), 1)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qa", default="eval/qa.jsonl")
    ap.add_argument("--generator", default="extractive", choices=["extractive", "ollama", "none"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--modes", default="vector,bm25,hybrid")
    ap.add_argument("--out", default="eval/results.json")
    args = ap.parse_args()

    s = load_settings()
    index = Index.load(s.index_dir, s.embedding_model)
    index.search("warmup", 1)  # first call loads the model, keep it out of the latency numbers
    rows = [json.loads(l) for l in open(args.qa) if l.strip()]
    if args.limit:
        rows = rows[: args.limit]
    gen = None
    if args.generator == "extractive":
        gen = ExtractiveGenerator()
    elif args.generator == "ollama":
        gen = OllamaGenerator(s.ollama_url, s.ollama_model)

    report = {"questions": len(rows), "chunks": len(index.chunks), "documents": len({c.source for c in index.chunks}),
              "embedding_model": s.embedding_model, "generator": args.generator, "modes": {}}
    for mode in args.modes.split(","):
        report["modes"][mode] = eval_mode(index, rows, mode, gen)
        print(mode, json.dumps(report["modes"][mode]))
    Path(args.out).write_text(json.dumps(report, indent=2))
    print("wrote", args.out)


if __name__ == "__main__":
    main()
