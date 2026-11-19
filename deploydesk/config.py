"""Per-customer configuration loaded from YAML (env var DEPLOYDESK_CONFIG)."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class Settings:
    customer_name: str = "Demo Customer"
    assistant_name: str = "DeployDesk"
    docs_dir: str = "data/raw"
    index_dir: str = "data/index"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    chunk_size: int = 800          # characters
    chunk_overlap: int = 150
    retrieval_mode: str = "hybrid"  # hybrid | vector | bm25
    top_k: int = 5
    rrf_k: int = 60
    generator: str = "extractive"  # extractive | ollama | gemini
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:0.5b"
    gemini_model: str = "gemini-1.5-flash"
    api_keys: list[str] = field(default_factory=lambda: ["dev-key-change-me"])
    log_file: str = "logs/requests.jsonl"


def load_settings(path: str | None = None) -> Settings:
    path = path or os.environ.get("DEPLOYDESK_CONFIG", "config/customer.yaml")
    data = {}
    if path and Path(path).exists():
        data = yaml.safe_load(Path(path).read_text()) or {}
    s = Settings(**{k: v for k, v in data.items() if k in Settings.__dataclass_fields__})
    env_keys = os.environ.get("DEPLOYDESK_API_KEYS")
    if env_keys:
        s.api_keys = [k.strip() for k in env_keys.split(",") if k.strip()]
    if os.environ.get("DEPLOYDESK_GENERATOR"):
        s.generator = os.environ["DEPLOYDESK_GENERATOR"]
    if os.environ.get("OLLAMA_URL"):
        s.ollama_url = os.environ["OLLAMA_URL"]
    return s
