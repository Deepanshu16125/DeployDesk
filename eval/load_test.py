"""Hit a running server with every eval question and report end-to-end HTTP latency."""
import json
import statistics
import sys
import time

import httpx

url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
key = sys.argv[2] if len(sys.argv) > 2 else "dev-key-change-me"
rounds = 3
questions = [json.loads(l)["q"] for l in open("eval/qa.jsonl") if l.strip()]

lat = []
with httpx.Client(base_url=url, headers={"X-API-Key": key}, timeout=120) as c:
    c.post("/ask", json={"question": "warmup request"})
    for _ in range(rounds):
        for q in questions:
            t0 = time.perf_counter()
            r = c.post("/ask", json={"question": q})
            r.raise_for_status()
            lat.append((time.perf_counter() - t0) * 1000)
lat.sort()
p = lambda x: round(lat[min(len(lat) - 1, int(round(x / 100 * (len(lat) - 1))))], 1)
print(json.dumps({"requests": len(lat), "p50_ms": p(50), "p95_ms": p(95), "p99_ms": p(99),
                  "mean_ms": round(statistics.mean(lat), 1)}))
