"""Clicks through every screen against a running server and fails loudly on any JS error.

    python tests/e2e/ui_flows.py http://localhost:8000 [--shots shots/]

Not part of the pytest run because it needs the real model behind Ollama.
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = next((a for a in sys.argv[1:] if a.startswith("http")), "http://localhost:8000")
SHOTS = Path(sys.argv[sys.argv.index("--shots") + 1]) if "--shots" in sys.argv else None
errors: list[str] = []
results: list[tuple[str, bool, str]] = []


def step(name):
    def wrap(fn):
        def run(page):
            t0 = time.time()
            try:
                fn(page)
                results.append((name, True, f"{time.time() - t0:.1f}s"))
            except Exception as e:
                results.append((name, False, str(e).splitlines()[0][:200]))
                page.screenshot(path=f"/tmp/e2e-fail-{name.replace(' ', '_')}.png")
        return run
    return wrap


def shot(page, name):
    if SHOTS:
        SHOTS.mkdir(exist_ok=True)
        page.screenshot(path=str(SHOTS / name))


@step("ask from suggestion, streamed")
def ask_suggestion(page):
    page.goto(BASE + "/#/ask")
    page.locator(".suggest button").first.click()
    page.wait_for_selector(".src-card", timeout=20000)
    page.wait_for_selector(".caret", timeout=30000)  # tokens are arriving
    shot(page, "02_streaming.png")
    page.wait_for_selector(".answer-foot", timeout=90000)
    expect(page.locator(".answer .cite").first).to_be_visible()
    assert "/#/c/" in page.url


@step("citation opens highlighted source")
def citation(page):
    page.locator(".answer .cite").first.click()
    expect(page.locator(".p-src.on")).to_be_visible()
    page.wait_for_timeout(400)
    shot(page, "03_answer_with_sources.png")


@step("thumbs down with reason")
def thumbs_down(page):
    page.locator('[data-rate="-1"]').last.click()
    page.locator('[id^="reasonIn-"]').fill("didn't mention the env var name")
    page.locator('[id^="reasonBtn-"]').click()
    expect(page.locator(".toast", has_text="logged")).to_be_visible()


@step("follow-up in same conversation")
def follow_up(page):
    url = page.url
    page.fill("#composer", "And how do I set that up in production?")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelectorAll('.answer-foot, .notfound').length >= 2", timeout=90000)
    assert page.url == url, (page.url, url)


@step("escalate to ticket")
def escalate(page):
    page.locator("#escTop").click()
    page.fill("#tkEmail", "dev@acme-shop.in")
    page.select_option("#tkPrio", "high")
    page.fill("#tkDetails", "We're on the Pro plan, region ap-south-1.")
    page.wait_for_timeout(450)
    shot(page, "07_escalate_modal.png")
    page.click("#tkCreate")
    expect(page.locator(".toast", has_text="created")).to_be_visible()
    expect(page.locator(".thread-top .pill", has_text="Ticket")).to_be_visible()


@step("off-topic question is refused")
def off_topic(page):
    page.click("#newChat")
    page.wait_for_selector(".hero")
    page.fill("#composer", "What's a good recipe for paneer butter masala?")
    page.keyboard.press("Enter")
    page.wait_for_selector(".notfound", timeout=60000)
    expect(page.locator(".notfound .btn")).to_be_visible()


@step("conversations list + search")
def conversations(page):
    page.goto(BASE + "/#/conversations")
    page.wait_for_selector("tr.click")
    n = page.locator("tr.click").count()
    page.fill("#convSearch", "paneer")
    page.wait_for_timeout(500)
    found = page.locator("tr.click").count()
    assert 1 <= found < n, "search did not filter"  # one paneer question per e2e run
    page.fill("#convSearch", "")
    page.wait_for_timeout(500)
    assert page.locator("tr.click").count() == n
    page.locator("tr.click").first.click()
    page.wait_for_selector(".answer, .notfound")


@step("ticket board + drawer update")
def tickets(page):
    page.goto(BASE + "/#/tickets")
    page.wait_for_selector(".tcard")
    page.locator(".tcard").first.click()
    page.select_option("#tStatus", "in_progress")
    page.fill("#tAssignee", "Priya Nair")
    page.fill("#tNote", "Answer is right for dev. Sent them the allow_origins example for their prod domain.")
    page.click("#tSave")
    expect(page.locator(".toast", has_text="updated")).to_be_visible()
    page.wait_for_selector(".col:nth-child(2) .tcard")
    page.locator(".col:nth-child(2) .tcard").first.click()
    expect(page.locator(".timeline .ev.note")).to_be_visible()
    page.wait_for_timeout(450)
    shot(page, "06_ticket_drawer.png")
    page.click("#dClose")


@step("upload, inspect, delete a document")
def kb(page):
    page.goto(BASE + "/#/kb")
    page.wait_for_selector("tr.click")
    f = Path("/tmp/kestrel-status-page.md")
    f.write_text("# Kestrel status page\n\n## Incident response times\n\n"
                 "Sev-1 incidents are acknowledged within 15 minutes, 24x7. Sev-2 within 2 business hours.\n")
    page.set_input_files("#fileIn", str(f))
    expect(page.locator(".up-row .pill.teal")).to_be_visible(timeout=30000)
    page.wait_for_timeout(450)
    shot(page, "05_knowledge_base.png")
    page.fill("#docSearch", "kestrel-status")
    page.wait_for_selector("tr.click")
    page.locator("tr.click").first.click()
    expect(page.locator(".chunk").first).to_contain_text("15 minutes")
    page.click("#dClose")
    page.once("dialog", lambda d: d.accept())
    page.locator("[data-del]").first.click()
    expect(page.locator(".toast", has_text="Removed")).to_be_visible()


@step("analytics renders charts")
def analytics(page):
    page.goto(BASE + "/#/analytics")
    page.wait_for_selector(".chart svg rect.bar", state="attached", timeout=15000)
    assert page.locator(".kpi").count() == 6
    page.wait_for_timeout(700)
    shot(page, "04_analytics.png")


@step("settings save")
def settings(page):
    page.goto(BASE + "/#/settings")
    page.wait_for_selector("#saveSettings")
    old = page.input_value('[data-k="top_k"]')
    page.fill('[data-k="top_k"]', "6")
    page.click("#saveSettings")
    expect(page.locator(".toast", has_text="saved")).to_be_visible()
    page.fill('[data-k="top_k"]', old)
    page.click("#saveSettings")
    shot(page, "08_settings.png")


@step("empty ask screen")
def empty(page):
    page.goto(BASE + "/#/ask")
    page.click("#newChat")
    page.wait_for_selector(".hero")
    page.wait_for_timeout(300)
    shot(page, "01_ask_empty.png")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))
        for fn in (empty, ask_suggestion, citation, thumbs_down, follow_up, escalate, off_topic,
                   conversations, tickets, kb, analytics, settings):
            fn(page)
        browser.close()
    for name, ok, info in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:40s} {info}")
    for e in errors:
        print("JS", e)
    passed = sum(ok for _, ok, _ in results)
    print(f"\n{passed}/{len(results)} flows passed, {len(errors)} JS errors")
    sys.exit(0 if passed == len(results) and not errors else 1)


if __name__ == "__main__":
    main()
