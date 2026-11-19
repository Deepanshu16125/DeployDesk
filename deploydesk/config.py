from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml


@dataclass
class Settings:
    customer_name: str = "Demo Customer"
    assistant_name: str = "DeployDesk"
    greeting: str = "Ask anything about the docs. Every answer links back to its source."
    suggested_questions: list[str] = field(default_factory=list)
    docs_dir: str = "data/raw"
    index_dir: str = "data/index"
    db_path: str = "data/deploydesk.db"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    chunk_size: int = 800
    chunk_overlap: int = 150
    retrieval_mode: str = "hybrid"
    rerank: bool = True
    top_k: int = 5
    context_k: int = 4
    rrf_k: int = 60
    min_rerank_score: float = -4.0
    generator: str = "extractive"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-3b-local"
    gemini_model: str = "gemini-1.5-flash"
    api_keys: list[str] = field(default_factory=lambda: ["dev-key-change-me"])
    demo_mode: bool = False
    log_file: str = "logs/requests.jsonl"
    config_path: str = ""

    def public(self) -> dict:
        d = asdict(self)
        d.pop("api_keys")
        return d


# fields the Settings page is allowed to change at runtime
EDITABLE = {"customer_name", "assistant_name", "greeting", "suggested_questions", "retrieval_mode", "rerank",
            "top_k", "context_k", "min_rerank_score", "generator", "ollama_model"}


def load_settings(path: str | None = None) -> Settings:
    path = path or os.environ.get("DEPLOYDESK_CONFIG", "config/customer.yaml")
    data = {}
    if path and Path(path).exists():
        data = yaml.safe_load(Path(path).read_text()) or {}
    s = Settings(**{k: v for k, v in data.items() if k in Settings.__dataclass_fields__})
    s.config_path = path or ""
    if os.environ.get("DEPLOYDESK_API_KEYS"):
        s.api_keys = [k.strip() for k in os.environ["DEPLOYDESK_API_KEYS"].split(",") if k.strip()]
    if os.environ.get("DEPLOYDESK_GENERATOR"):
        s.generator = os.environ["DEPLOYDESK_GENERATOR"]
    if os.environ.get("OLLAMA_URL"):
        s.ollama_url = os.environ["OLLAMA_URL"]
    if os.environ.get("OLLAMA_MODEL"):
        s.ollama_model = os.environ["OLLAMA_MODEL"]
    return s


def save_settings(s: Settings) -> None:
    if not s.config_path:
        return
    p = Path(s.config_path)
    data = yaml.safe_load(p.read_text()) if p.exists() else {}
    data = data or {}
    for k in EDITABLE:
        data[k] = getattr(s, k)
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
