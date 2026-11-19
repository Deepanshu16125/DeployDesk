# DeployDesk handover note

To: Acme Platform Team
From: Deepanshu
Status: draft, v0.1 (in progress)

Hi team,

This is the short version of what you're getting, how to run it, and what to watch. The longer runbook is in the README.

**What it does.** DeployDesk answers questions from your own docs and shows which file (and page or section) each part of the answer came from. Right now it's loaded with 67 documents (FastAPI and Uvicorn docs, Pydantic settings/validators pages, and two NIST PDFs) as a stand-in for your knowledge base, split into 1,461 chunks. Nothing leaves your network: the embedding model runs locally and the default answer mode doesn't call any LLM.

**How good is it.** I built a test set of 63 questions with the file each answer should come from. With the current settings (hybrid search, top 5) the right passage is in the top 5 results for 90.5% of questions, and it's the first result 73% of the time. On the same set, vector-only search got the first result right 58.7% of the time, which is why hybrid is the default. Median end-to-end response time over HTTP was about 11 ms on an 8-core VM with no GPU. Re-run `python eval/run_eval.py` after you swap in your docs; the numbers above are for the demo set, not yours.

**Running it.**
1. Put your PDFs, Markdown and HTML files in `data/raw/`.
2. Set a real API key: `export DEPLOYDESK_API_KEYS=<something long>`.
3. `docker compose up -d --build`, then `curl -X POST localhost:8000/ingest -H "X-API-Key: $DEPLOYDESK_API_KEYS"`.
4. Check `curl localhost:8000/health` says `"status": "ok"`. The chat page is at `/`, the numbers at `/dashboard`.

**Things you'll want to change.** Everything customer-specific is in `config/customer.yaml` (name, doc folder, chunk size, top_k, which answer generator). Copy that file per team rather than editing code.

**Known limits, so nobody is surprised.**
- The default answers are extractive: they stitch together the most relevant sentences with citations. They're accurate to the source but can read choppy. Switching `generator` to `ollama` gives written answers but is much slower on CPU.
- The Gemini / Vertex AI option is a stub. It works if you set `GEMINI_API_KEY`, but I haven't tested it against a real key.
- Re-ingest rebuilds the whole index (about 20 s for the demo set). Fine for hundreds of files, not for tens of thousands.
- There's no user management, just API keys. Put it behind your SSO/reverse proxy if it goes beyond your team.
- Tables and scanned images in PDFs don't come through well (no OCR).

**If something breaks.** Every request is logged as one JSON line in `logs/requests.jsonl` with a request id, status and latency; the same id is in the `X-Request-ID` response header, so send me that id with any bad answer. `/metrics` shows live p50/p95.

Thanks,
Deepanshu
