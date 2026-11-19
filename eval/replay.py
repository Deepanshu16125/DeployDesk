"""Sends a list of questions through the streaming endpoint the way the UI does and records
client-side time to first token and total time. Also fills history/analytics with real runs.

Use questions the model hasn't seen yet: Ollama reuses its prompt cache for repeated prompts,
which makes first-token time look ~10x better than it is."""
import json
import sys
import time

import httpx

url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
src = sys.argv[2] if len(sys.argv) > 2 else "eval/demo_questions.txt"
key = "dev-key-change-me"
rows = [l.strip() for l in open(src) if l.strip()]
out = []
with httpx.Client(base_url=url, headers={"X-API-Key": key}, timeout=180) as c:
    for q in rows:
        t0 = time.perf_counter()
        first = None
        with c.stream("POST", "/api/ask/stream", json={"question": q}) as r:
            for line in r.iter_lines():
                if line.startswith("event: token") and first is None:
                    first = time.perf_counter() - t0
        out.append({"q": q, "client_ttft_ms": round((first or 0) * 1000), "client_total_ms": round((time.perf_counter() - t0) * 1000)})
        print(out[-1], flush=True)
tt = sorted(x["client_ttft_ms"] for x in out if x["client_ttft_ms"])
tot = sorted(x["client_total_ms"] for x in out)
p = lambda s, q: s[min(len(s) - 1, int(round(q / 100 * (len(s) - 1))))]
summary = {"requests": len(out), "client_ttft_p50_ms": p(tt, 50), "client_ttft_p95_ms": p(tt, 95),
           "client_total_p50_ms": p(tot, 50), "client_total_p95_ms": p(tot, 95)}
print(json.dumps(summary))
json.dump(summary, open("eval/replay_result.json", "w"), indent=2)
