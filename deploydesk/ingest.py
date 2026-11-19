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
    ext = path.suffix.lower()
    if ext == ".pdf":
        reader = PdfReader(str(path))
        return [(f"page {i + 1}", _clean(_unwrap_pdf(p.extract_text() or ""))) for i, p in enumerate(reader.pages)]
    if ext in (".html", ".htm"):
        soup = BeautifulSoup(path.read_text(errors="ignore"), "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        main = soup.find("article") or soup.find("main") or soup.body or soup
        for a in main.select("a.headerlink"):
            a.decompose()
        for h in main.find_all(["h1", "h2", "h3"]):
            h.replace_with(f"\n\n## {h.get_text(' ', strip=True)}\n")
        # get_text("\n") puts every inline tag on its own line, so add breaks only after block tags
        for tag in main.find_all(["p", "li", "pre", "tr", "br", "dt", "dd", "table", "ul", "ol", "div"]):
            tag.append("\n")
        text = re.sub(r"[ \t]*\n[ \t]*", "\n", main.get_text(""))
        return _split_sections(_strip_markdown(text), heading_re=r"^## +(.+)$")
    text = path.read_text(errors="ignore")
    # drop fenced-code language markers / admonition syntax but keep code text
    text = re.sub(r"\{\*[^}]*\*\}", "", text)
    return _split_sections(_strip_markdown(text), heading_re=r"^#{1,4} +(.+)$")


def _unwrap_pdf(text: str) -> str:
    # pypdf keeps the PDF's visual line breaks: "sys-\ntem", sentences split across lines
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    return re.sub(r"(?<![.:;!?\n])\n(?![\n•●\-–]|[A-Z]{2,}\b)", " ", text)


def _strip_markdown(text: str) -> str:
    # keep headings (the splitter needs them) but drop syntax that only adds noise to embeddings and the UI
    text = re.sub(r"\s*\{\s*#[^}]*\}", "", text)          # mkdocs anchors
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)    # [text](url) -> text
    text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"\1", text)  # **bold**; not __x__, that's usually a dunder
    text = re.sub(r"^///.*$|^!!!.*$", "", text, flags=re.M)  # admonition fences
    text = re.sub(r"</?(details|summary|div|span|br)[^>]*>", "", text)
    text = text.replace("¶", "")
    return text


def _split_sections(text: str, heading_re: str | None) -> list[tuple[str, str]]:
    if not heading_re:
        return [("document", _clean(text))]
    sections, current, buf = [], "intro", []
    in_code = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        m = None if in_code else re.match(heading_re, line)  # "# comment" inside code is not a heading
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
        nxt = max(end - overlap, start + 1)
        # start the overlap at a sentence or word boundary, not mid-word ("ar origins, methods...")
        sent = max(text.rfind(". ", nxt - overlap // 2, nxt + overlap // 2), text.rfind("\n", nxt - overlap // 2, nxt + overlap // 2))
        if sent > start:
            nxt = sent + 1
        else:
            space = text.find(" ", nxt, end)
            nxt = space + 1 if space != -1 else nxt
        start = nxt
    return [c for c in chunks if c]


def ingest_file(path: Path, rel: str, size: int = 800, overlap: int = 150) -> list[Chunk]:
    chunks: list[Chunk] = []
    for loc, text in load_document(path):
        for piece in chunk_text(text, size, overlap):
            if len(piece) < 40:
                continue
            cid = hashlib.sha1(f"{rel}|{loc}|{len(chunks)}|{piece[:80]}".encode()).hexdigest()[:12]
            chunks.append(Chunk(cid, rel, loc, piece))
    return chunks


def list_files(docs_dir: str | Path) -> list[Path]:
    return sorted(p for p in Path(docs_dir).rglob("*") if p.suffix.lower() in SUPPORTED)


def ingest_dir(docs_dir: str | Path, size: int = 800, overlap: int = 150) -> tuple[list[Chunk], int]:
    docs_dir = Path(docs_dir)
    files = list_files(docs_dir)
    chunks: list[Chunk] = []
    for f in files:
        chunks.extend(ingest_file(f, str(f.relative_to(docs_dir)), size, overlap))
    return chunks, len(files)
