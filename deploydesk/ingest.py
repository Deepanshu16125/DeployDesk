"""Load PDF / Markdown / HTML documents and split them into overlapping chunks."""
from __future__ import annotations
import hashlib
import re
from dataclasses import dataclass, asdict
from pathlib import Path

from bs4 import BeautifulSoup
from pypdf import PdfReader

SUPPORTED = {".pdf", ".md", ".markdown", ".html", ".htm", ".txt"}


@dataclass
class Chunk:
    id: str
    source: str
    location: str   # page number for PDFs, section heading otherwise
    text: str

    def to_dict(self):
        return asdict(self)


def _clean(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def load_document(path: Path) -> list[tuple[str, str]]:
    """Return a list of (location, text) sections for one file."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        reader = PdfReader(str(path))
        return [(f"page {i + 1}", _clean(p.extract_text() or "")) for i, p in enumerate(reader.pages)]
    if ext in (".html", ".htm"):
        soup = BeautifulSoup(path.read_text(errors="ignore"), "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        main = soup.find("article") or soup.find("main") or soup.body or soup
        return _split_sections(main.get_text("\n"), heading_re=None)
    text = path.read_text(errors="ignore")
    # drop fenced-code language markers / admonition syntax but keep code text
    text = re.sub(r"\{\*[^}]*\*\}", "", text)
    return _split_sections(text, heading_re=r"^#{1,4} +(.+)$")


def _split_sections(text: str, heading_re: str | None) -> list[tuple[str, str]]:
    if not heading_re:
        return [("document", _clean(text))]
    sections, current, buf = [], "intro", []
    for line in text.splitlines():
        m = re.match(heading_re, line)
        if m:
            if buf:
                sections.append((current, _clean("\n".join(buf))))
            # mkdocs headings carry an anchor like { #use-corsmiddleware }
            current, buf = re.sub(r"\s*\{\s*#[^}]*\}", "", m.group(1)).strip(), [line]
        else:
            buf.append(line)
    if buf:
        sections.append((current, _clean("\n".join(buf))))
    return [(loc, t) for loc, t in sections if t]


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):  # try to break on a paragraph / sentence boundary
            cut = max(text.rfind("\n\n", start, end), text.rfind(". ", start, end))
            if cut > start + size // 2:
                end = cut + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


def ingest_dir(docs_dir: str | Path, size: int = 800, overlap: int = 150) -> tuple[list[Chunk], int]:
    docs_dir = Path(docs_dir)
    files = sorted(p for p in docs_dir.rglob("*") if p.suffix.lower() in SUPPORTED)
    chunks: list[Chunk] = []
    for f in files:
        rel = str(f.relative_to(docs_dir))
        for loc, text in load_document(f):
            for piece in chunk_text(text, size, overlap):
                if len(piece) < 40:
                    continue
                cid = hashlib.sha1(f"{rel}|{loc}|{piece[:80]}|{len(chunks)}".encode()).hexdigest()[:12]
                chunks.append(Chunk(cid, rel, loc, piece))
    return chunks, len(files)
