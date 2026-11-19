from pathlib import Path

from deploydesk.generator import ExtractiveGenerator, GeminiGenerator, answer_with_fallback
from deploydesk.ingest import Chunk, chunk_text, ingest_dir, load_document

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_chunk_overlap_and_coverage():
    text = ("Sentence number %d is here. " * 200) % tuple(range(200))
    chunks = chunk_text(text, 300, 50)
    assert len(chunks) > 5
    assert all(len(c) <= 300 for c in chunks)
    # every sentence should survive chunking somewhere
    for i in (0, 57, 199):
        assert any(f"number {i} " in c for c in chunks)


def test_short_text_single_chunk():
    assert chunk_text("hello world", 800, 150) == ["hello world"]


def test_markdown_sections_keep_headings():
    sections = load_document(FIXTURES / "refunds.md")
    assert any(loc == "Refund window" for loc, _ in sections)


def test_html_strips_nav_and_scripts():
    (_, text), = load_document(FIXTURES / "gateway.html")
    assert "8443" in text
    assert "trackingCode" not in text
    assert "Home | Pricing" not in text


def test_pdf_pages():
    pages = load_document(FIXTURES / "sla.pdf")
    assert pages[0][0] == "page 1"
    assert "99.9" in pages[0][1]


def test_ingest_dir_counts():
    chunks, n = ingest_dir(FIXTURES)
    assert n == 3
    assert {c.source for c in chunks} == {"refunds.md", "gateway.html", "sla.pdf"}


def test_extractive_cites_and_handles_no_match():
    c = [Chunk("x1", "a.md", "intro", "The refund window is 30 days from delivery. Shipping is free.")]
    out = ExtractiveGenerator().answer("how long is the refund window", c)
    assert "[1]" in out["answer"] and out["citations"][0]["source"] == "a.md"
    none = ExtractiveGenerator().answer("zebra quantum", c)
    assert none["citations"] == []


def test_normalize_citations():
    from deploydesk.generator import normalize_citations
    assert normalize_citations("Do X. [3] Then Y [1, 2].") == "Do X [3]. Then Y [1] [2]."


def test_markdown_cleanup_keeps_dunders():
    from deploydesk.ingest import _strip_markdown
    out = _strip_markdown('Run **uvicorn** with `if __name__ == "__main__":` see [docs](https://x.y) { #anchor }')
    assert out == 'Run uvicorn with `if __name__ == "__main__":` see docs'


def test_code_comments_are_not_headings():
    from deploydesk.ingest import _split_sections
    secs = _split_sections("# Title\n\ntext\n\n```\n# not a heading\nx = 1\n```\n", r"^#{1,4} +(.+)$")
    assert [loc for loc, _ in secs] == ["Title"]


def test_highlights_point_at_supporting_sentence():
    from deploydesk.highlight import highlights
    c = [Chunk("x1", "a.md", "intro", "Shipping is free. The refund window is 30 days from delivery. Call us anytime.")]
    spans = highlights("Refunds can be requested within 30 days of delivery [1].", c, [1])
    start, end = spans[1][0]
    assert "30 days" in c[0].text[start:end]


def test_gemini_stub_falls_back(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    c = [Chunk("x1", "a.md", "intro", "The refund window is 30 days from delivery.")]
    out = answer_with_fallback(GeminiGenerator("gemini-1.5-flash"), "refund window days", c)
    assert out["generator"] == "extractive"
    assert "GEMINI_API_KEY" in out["fallback_reason"]
