"""End-to-end answer eval with the local LLM.

Groundedness is checked two ways for every answer sentence that carries a [n] citation:
- NLI: supported if an NLI cross-encoder (deberta-v3-base) says a cited chunk entails it.
  Strict: it marks most paraphrases and partial sentences as "neutral".
- judge: supported if the local LLM answers "yes" when asked whether the cited chunk states it.
  Lenient, and it's the same model family grading itself.
The truth is somewhere between the two, so I report both.
"""
import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sentence_transformers import CrossEncoder

from deploydesk.config import load_settings
from deploydesk.generator import NOT_FOUND, OllamaGenerator, cited_refs
from deploydesk.retriever import Index
from run_eval import is_relevant, pct

OFF_TOPIC = [
    "What is the capital of France?", "Who won the 2022 football world cup?",
    "Give me a recipe for paneer butter masala", "What's the weather in Mumbai tomorrow?",
    "How do I reset my Kestrel billing password?", "What is the price of the enterprise plan?",
    "Can you write me a poem about clouds?", "How tall is Mount Everest?",
    "What is our refund policy for annual plans?", "How do I cancel my Kestrel subscription?",
]


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip(" -*") for p in parts if len(re.sub(r"\[\d+\]", "", p).strip()) > 15]


JUDGE = """Passage:
{passage}

Claim: {claim}

Is every fact in the claim stated or directly implied by the passage? Answer only "yes" or "no"."""


def judge_supported(url: str, model: str, claim: str, passages: list[str]) -> bool:
    import httpx
    for p in passages:
        r = httpx.post(f"{url}/api/generate", json={"model": model, "prompt": JUDGE.format(passage=p, claim=claim),
                                                    "stream": False, "options": {"temperature": 0, "num_predict": 3}}, timeout=120)
        if r.json()["response"].strip().lower().startswith("yes"):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="eval/results_llm.json")
    ap.add_argument("--judge", default="", help="ollama model to use as a second grader, e.g. qwen2.5-3b-local (slow)")
    args = ap.parse_args()

    s = load_settings()
    model = args.model or s.ollama_model
    index = Index.load(s.index_dir, s.embedding_model, s.reranker_model)
    index.search("warmup", 1, rerank=True)
    gen = OllamaGenerator(s.ollama_url, model, s.customer_name)
    nli = CrossEncoder("cross-encoder/nli-deberta-v3-base", device="cpu")
    labels = {v.lower(): k for k, v in nli.model.config.id2label.items()}
    entail = labels["entailment"]

    rows = [json.loads(l) for l in open("eval/qa.jsonl") if l.strip()]
    if args.limit:
        rows = rows[: args.limit]
    gen.stream("warm up the model", [])  # load weights before timing
    list(gen.stream("hi", []))

    records = []
    for row in rows + [{"q": q, "off_topic": True} for q in OFF_TOPIC]:
        t0 = time.perf_counter()
        hits = index.search(row["q"], k=s.top_k, mode=s.retrieval_mode, rerank=s.rerank)
        chunks = [c for c, _ in hits][: s.context_k]
        score = hits[0][1] if s.rerank and hits else index.relevance(row["q"], chunks)
        if score is not None and score < s.min_rerank_score:
            answer, stats = NOT_FOUND, {}
        else:
            answer = gen.answer(row["q"], chunks)["answer"]
            stats = gen.last_stats
        total = (time.perf_counter() - t0) * 1000
        refused = answer.strip().startswith(NOT_FOUND[:25])
        rec = {"q": row["q"], "off_topic": row.get("off_topic", False), "answer": answer, "refused": refused,
               "relevance": score, "total_ms": round(total, 1), **stats}
        if not rec["off_topic"] and not refused:
            refs = cited_refs(answer, len(chunks))
            sents = split_sentences(answer)
            cited_sents = [(x, [int(n) for n in re.findall(r"\[(\d+)\]", x) if int(n) in refs]) for x in sents]
            cited_sents = [(x, r) for x, r in cited_sents if r]
            supported = judged = 0
            for sent, rs in cited_sents:
                claim = re.sub(r"\[\d+\]", "", sent).strip()
                logits = nli.predict([(chunks[r - 1].text, claim) for r in rs], apply_softmax=True, show_progress_bar=False)
                if max(float(p[entail]) for p in logits) > 0.5:
                    supported += 1
                if args.judge and judge_supported(s.ollama_url, args.judge, claim, [chunks[r - 1].text for r in rs]):
                    judged += 1
            rec.update({"n_sentences": len(sents), "n_cited_sentences": len(cited_sents), "n_supported": supported,
                        "n_judge_supported": judged,
                        "has_citation": bool(refs),
                        "cites_gold": any(is_relevant(chunks[r - 1], row) for r in refs),
                        "gold_in_context": any(is_relevant(c, row) for c in chunks),
                        "key_in_answer": row["key"].lower() in answer.lower()})
        records.append(rec)
        print(json.dumps({k: rec.get(k) for k in ("q", "refused", "total_ms", "ttft_ms", "tokens_per_s", "n_supported", "n_cited_sentences")}), flush=True)

    on = [r for r in records if not r["off_topic"]]
    answered = [r for r in on if not r["refused"]]
    off = [r for r in records if r["off_topic"]]
    cited_total = sum(r["n_cited_sentences"] for r in answered)
    report = {
        "model": model, "rerank": s.rerank, "questions": len(on), "off_topic_questions": len(off),
        "answered": len(answered), "false_refusals": len(on) - len(answered),
        "off_topic_refused": sum(r["refused"] for r in off),
        "answers_with_citation": round(sum(r["has_citation"] for r in answered) / len(answered), 3),
        "sentences_with_citation": round(cited_total / max(1, sum(r["n_sentences"] for r in answered)), 3),
        "cited_sentences_supported_nli": round(sum(r["n_supported"] for r in answered) / max(1, cited_total), 3),
        "cited_sentences_supported_judge": round(sum(r["n_judge_supported"] for r in answered) / max(1, cited_total), 3) if args.judge else None,
        "judge_model": args.judge,
        "cites_correct_source": round(sum(r["cites_gold"] for r in answered) / len(answered), 3),
        "gold_passage_in_context": round(sum(r["gold_in_context"] for r in answered) / len(answered), 3),
        "key_phrase_in_answer": round(sum(r["key_in_answer"] for r in answered) / len(answered), 3),
        "ttft_p50_ms": pct([r["ttft_ms"] for r in answered if r.get("ttft_ms")], 50),
        "ttft_p95_ms": pct([r["ttft_ms"] for r in answered if r.get("ttft_ms")], 95),
        "tokens_per_s_p50": pct([r["tokens_per_s"] for r in answered if r.get("tokens_per_s")], 50),
        "answer_tokens_mean": round(statistics.mean([r["tokens"] for r in answered if r.get("tokens")]), 1),
        "total_p50_ms": pct([r["total_ms"] for r in answered], 50),
        "total_p95_ms": pct([r["total_ms"] for r in answered], 95),
    }
    Path(args.out).write_text(json.dumps(report, indent=2))
    Path(args.out.replace(".json", "_answers.jsonl")).write_text("\n".join(json.dumps(r) for r in records))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
