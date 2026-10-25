"""Infrastructure settings, read from environment variables (``RAG_*``) and an optional ``.env`` file.

These describe *where things run* (URLs, paths, ports, secrets). What a chatbot *does* lives in
``config.KnowledgeBaseConfig`` and is versioned per knowledge base.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class Settings(BaseModel):
    state_dir: Path = Path(".ragkit")
    data_roots: list[Path] = Field(default_factory=lambda: [Path("data/sources")])
    qdrant_mode: Literal["server", "embedded"] = "embedded"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    ollama_url: str = "http://localhost:11434"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str | None = None
    rerank_provider: Literal["fastembed", "none"] = "fastembed"
    model_cache_dir: Path | None = None
    api_key: str | None = None
    bind_host: str = "127.0.0.1"
    # Containers must bind 0.0.0.0; exposure is then controlled by published ports (127.0.0.1 by default).
    public_bind_ok: bool = False
    port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://localhost:8080"])
    log_format: Literal["text", "json"] = "text"
    log_level: str = "INFO"
    ui_dist_dir: Path | None = None
    max_upload_mb: int = 100
    request_timeout_s: float = 300.0
    # Service level objectives, evaluated from stored traces (/api/slo) and mirrored in Prometheus rules.
    slo_availability: float = Field(0.99, gt=0, lt=1)
    slo_latency_target: float = Field(0.95, gt=0, lt=1)
    slo_latency_seconds: float = Field(5.0, gt=0)
    slo_window_days: int = Field(7, ge=1, le=90)

    @property
    def db_path(self) -> Path:
        return self.state_dir / "ragkit.db"

    @property
    def uploads_dir(self) -> Path:
        return self.state_dir / "uploads"

    @property
    def embedded_qdrant_path(self) -> Path:
        return self.state_dir / "qdrant"

    @property
    def is_loopback(self) -> bool:
        return self.bind_host in {"127.0.0.1", "localhost", "::1"}

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        source = dict(os.environ if env is None else env)
        values: dict[str, object] = {}
        for name, field in cls.model_fields.items():
            raw = source.get(f"RAG_{name.upper()}")
            if raw is None or raw == "":
                continue
            values[name] = [item.strip() for item in raw.split(",") if item.strip()] if _is_list(field) else raw
        return cls.model_validate(values)


def _is_list(field: object) -> bool:
    return "list" in str(getattr(field, "annotation", ""))


def load_dotenv(path: Path = Path(".env")) -> None:
    """Load ``KEY=VALUE`` lines into ``os.environ`` without overriding variables already set."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key, value.strip().strip("'\""))
