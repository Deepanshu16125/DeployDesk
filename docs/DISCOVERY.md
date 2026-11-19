# Discovery notes: Kestrel Cloud support desk

*Kestrel Cloud is a made-up customer. I wanted the project to have the shape of a real engagement (a team with a problem, constraints, a way to say "this worked") instead of "RAG over some PDFs", so I wrote the brief I'd expect to get and worked against it. The problem and constraints are invented; everything under "What I delivered" is real and measured.*

## Who they are

Kestrel runs a small hosting platform for Python web APIs, mostly FastAPI apps on Uvicorn. Around 40 people. Support is four engineers on a shared inbox, plus one person in security/compliance who answers customer security questionnaires.

## The problem, as they'd put it

> "Half of what comes in is already answered in the docs. People ask how to set CORS, how many workers to run in a container, why their env vars aren't loading. We paste the same links all day. The other big chunk is enterprise prospects sending security questionnaires that map to NIST, and only one person knows where everything is."

What I took from that:

1. Repetitive "how do I" questions where the answer exists but is hard to find.
2. Compliance questions where the exact source matters more than the wording. Nobody can paste an answer into a questionnaire without knowing where it came from.
3. When the bot doesn't know, the question has to reach a person with the context attached, not just die in a chat window.

## Requirements we agreed on

| # | Requirement | Why |
|---|---|---|
| R1 | Answers come only from Kestrel's own docs, each claim linked to its source | Compliance can't use an unsourced answer |
| R2 | Say "I don't know" instead of guessing on off-topic questions | One confidently wrong answer about security costs more than ten unanswered ones |
| R3 | Runs inside their network. No customer data or docs go to a third-party API | Their enterprise contracts forbid it |
| R4 | Support can add or remove documents themselves, no engineer needed | Docs change weekly |
| R5 | Escalate to a human with the question, bot answer and sources attached | The inbox is the fallback, so it has to keep working |
| R6 | A dashboard showing usage, speed and where the docs have gaps | So they can tell if it's worth keeping |
| R7 | Plain REST API so they can wire it into their helpdesk and Slack later | They don't want another tab long term |

## Success metrics (set before building)

- Retrieval: the right passage in the top 5 for at least 85% of a labelled question set.
- Refusals: at least 9 out of 10 off-topic questions refused, and no more than 5% of real questions wrongly refused.
- Every answer carries at least one citation.
- First token in under 3 s on their existing CPU-only VM (no GPU budget).

## What I delivered

| Req | What exists | Measured |
|---|---|---|
| R1 | Hybrid retrieval (BM25 + MiniLM vectors, RRF) → cross-encoder rerank → local Qwen2.5-3B with inline [n] citations; source panel highlights the supporting sentence | see `eval/results*.json` and README |
| R2 | Cross-encoder relevance threshold (−4.0) blocks the LLM call when nothing relevant comes back | see README results |
| R3 | Everything runs locally: embeddings, reranker, LLM via Ollama. Docker image bakes the models in | no outbound calls at request time |
| R4 | Knowledge base page: drag-and-drop upload, incremental indexing, per-file delete, view every passage | upload to searchable in about a second for a small file |
| R5 | "Escalate" on any answer creates a ticket with question, answer and sources; ticket board with status, assignee and notes | tested end to end |
| R6 | Analytics page: volume, latency histogram, first-token time, eval by mode, answer quality, most-cited docs, unanswered questions, thumbs-down comments | live from SQLite |
| R7 | `/ask` (JSON) and `/api/ask/stream` (SSE), API-key auth, OpenAPI docs at `/docs` | |

## What's next if this were real

- Sit with the support team for a day and collect 50 real questions from the inbox. My eval set is written by me from the same docs, so it's easier than real traffic.
- Pick the model with them: 3B is accurate enough on citations but some answers read stiff. A 7B model would need roughly double the RAM and be about half the speed on this CPU.
- Hook the ticket flow into whatever helpdesk they actually use instead of the built-in board.
- Weekly "gaps" report from the unanswered list and thumbs-down comments to the docs owner.
