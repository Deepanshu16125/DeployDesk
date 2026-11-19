# Handover: Kestrel Help (DeployDesk v0.2)

To: Kestrel support team (Priya, Arjun, Sam, Meera) and Tom on platform
From: Deepanshu
Date: 19 Nov 2026

Hi all,

Here's where the help desk stands, how to run it, and what I'd keep an eye on. The README has the full runbook and the eval details. This note covers the things you'll actually hit.

**What you're getting.** A help desk that answers from your docs. Right now that's the FastAPI, Uvicorn and Pydantic pages you send customers to, plus the two NIST documents Meera uses for security questionnaires: 67 files, 1,409 passages. Answers stream in with numbered citations, and clicking a number shows the exact passage with the supporting sentence highlighted. If it can't find a good passage it says so and offers to hand the question to one of you. That creates a ticket on the Tickets board with the question, its answer and the sources already attached. Everything runs on your own machine, including the language model, so no docs or customer questions go to an outside API.

**How well it works on the questions I tested.** I wrote 63 questions with you in mind and labelled where each answer lives. The right passage is in the top five results 92% of the time. Every answer cites something, and it refused all 10 off-topic questions I tried (billing, pricing, the weather) without wrongly refusing any real one. The weak spot is wording: the model is small, so it sometimes blends two passages or paraphrases loosely. An automatic check agreed that only about 43% of cited sentences are fully backed by their passage. Please read the cited passage before pasting anything into a questionnaire. That's why the highlight is there.

Speed on an 8-core server with no GPU: words start appearing after about 2 seconds, and a full answer takes about 4.5 seconds.

**Day to day.**
- New or updated doc: drop it on the Knowledge base page. It's searchable within a second. Deleting a doc there removes it from answers straight away.
- Thumbs down asks for a reason. Those reasons and the questions it couldn't answer show up under Analytics → "Gaps in the docs". I'd go through that list once a week, because it's the most useful output of the whole thing.
- Tickets move open → in progress → waiting → resolved. Notes are internal only.

**Running it.** `docker compose up -d --build`, then once `docker compose --profile setup run --rm ollama-pull` to download the model (about 2 GB). Set `DEPLOYDESK_API_KEYS` to a long random value first, and set `demo_mode: false` in `config/customer.yaml`. Demo mode puts the key in the page so the demo works without a login, which is fine for a laptop and not fine on the network. The app container starts in about 5 seconds. Note that I tested the container with host networking on my VM. The compose file is written for a normal Docker host, but I haven't run that exact setup yet, so please tell me if it gives you trouble.

**Things I'd change before wider rollout.**
- Put it behind your SSO. API keys are the only auth right now, and the tickets board has no per-user login.
- The "refuse" threshold (`min_rerank_score: -6.5`) was tuned on my questions. Once you have a week of real traffic, check the refused ones in Analytics and move it if real questions are getting blocked.
- Follow-up questions work for simple cases ("and in production?") but it only remembers the previous question, not the whole conversation.
- Scanned PDFs need OCR, which it doesn't do. Tables in PDFs come through as flat text.
- If you're allowed a cloud model later, there's a Gemini option in config. I haven't tested it with a real key.

**If something goes wrong.** Every request writes one line to `logs/requests.jsonl` with a request id, and the same id is in the `X-Request-ID` response header. Send me the id with any bad answer and I can see exactly what it retrieved. `/health` and the dot at the bottom left of the app show whether the index is loaded. The Settings page lists the models Ollama can see, so check there if answers stop coming. If the model is down, answers fall back to quoting the best passages instead of failing.

Thanks for the time you spent on the discovery call. Happy to pair on the first week of real questions.

Deepanshu
