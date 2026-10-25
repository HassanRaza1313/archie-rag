"""Request traces and logging.

A ``Trace`` records stage spans (timings + attributes), the candidate funnel, agent decisions and the outcome.
It is persisted to SQLite (UI telemetry) and summarised as one log line, so logs stay readable while the
detail stays queryable. Span names and attributes follow OpenTelemetry conventions, so an OTLP exporter can be
added without changing call sites.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

REQUEST_LOGGER = logging.getLogger("ragkit.request")
# The current request (or build job) id; set by the HTTP middleware and the worker, read by logs and traces.
request_id_var: ContextVar[str | None] = ContextVar("ragkit_request_id", default=None)


@dataclass
class Trace:
    kb_id: str
    config_version: int
    query: str
    session_id: str | None = None
    level: str = "standard"
    id: str = field(default_factory=lambda: request_id_var.get() or uuid.uuid4().hex)
    started_at: float = field(default_factory=time.time)
    spans: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    funnel: dict[str, int] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    outcome: str = "error"
    cache_hit: bool = False
    confidence: float | None = None
    retries: int = 0
    verify: str | None = None
    ttft_ms: float | None = None
    tokens: dict[str, int] = field(default_factory=dict)
    error: dict[str, str] | None = None
    prompt: str | None = None
    _clock: float = field(default_factory=time.perf_counter, repr=False)

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._clock) * 1000

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[dict[str, Any]]:
        start = self.elapsed_ms()
        record: dict[str, Any] = {"name": name, "start_ms": round(start, 1), "attributes": dict(attributes)}
        try:
            yield record["attributes"]
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record["duration_ms"] = round(self.elapsed_ms() - start, 1)
            self.spans.append(record)

    def decide(self, decision: str) -> None:
        self.decisions.append(decision)

    def stage_ms(self, name: str) -> float:
        return round(sum(s["duration_ms"] for s in self.spans if s["name"] == name), 1)

    def to_record(self) -> dict[str, Any]:
        verbose = self.level == "verbose"
        candidates = (
            self.candidates if verbose else [{k: v for k, v in c.items() if k != "text"} for c in self.candidates]
        )
        return {
            "id": self.id,
            "kb_id": self.kb_id,
            "config_version": self.config_version,
            "session_id": self.session_id,
            "created_at": self.started_at,
            "outcome": self.outcome,
            "total_ms": round(self.elapsed_ms(), 1),
            "ttft_ms": round(self.ttft_ms, 1) if self.ttft_ms is not None else None,
            "cache_hit": int(self.cache_hit),
            "confidence": self.confidence,
            "retries": self.retries,
            "verify": self.verify,
            "query": self.query,
            "data": {
                "spans": self.spans,
                "decisions": self.decisions,
                "funnel": self.funnel,
                "candidates": candidates,
                "tokens": self.tokens,
                "error": self.error,
                "prompt": self.prompt if verbose else None,
            },
        }

    def log_line(self) -> str:
        funnel = self.funnel
        funnel_text = (
            f"{funnel.get('dense', 0)}/{funnel.get('keyword', 0)}>{funnel.get('fused', 0)}>"
            f"{funnel.get('relevant', 0)}>{funnel.get('deduplicated', 0)}>{funnel.get('context', 0)}"
            if funnel
            else "-"
        )
        parts = [
            f"rid={self.id}",
            f"kb={self.kb_id}",
            f"v={self.config_version}",
            f"outcome={self.outcome}",
            f"total={self.elapsed_ms() / 1000:.2f}s",
            f"ttft={self.ttft_ms / 1000:.2f}s" if self.ttft_ms is not None else "ttft=-",
            f"ret={self.stage_ms('retrieve'):.0f}ms",
            f"gen={self.stage_ms('generate'):.0f}ms",
            f"funnel={funnel_text}",
            f"conf={self.confidence:.2f}" if self.confidence is not None else "conf=-",
            f"retries={self.retries}",
            f"verify={self.verify or '-'}",
            f"cache={'hit' if self.cache_hit else 'miss'}",
        ]
        if self.error:
            parts.append(f"error={self.error['stage']}:{self.error['type']}")
        return "chat " + " ".join(parts)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


class CorrelationFilter(logging.Filter):
    """Stamp every record with the current request or job id, so one id finds all lines of one request."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


def configure_logging(level: str, log_format: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(CorrelationFilter())
    handler.setFormatter(
        JsonFormatter()
        if log_format == "json"
        else logging.Formatter("%(asctime)s %(levelname)-5s %(name)s rid=%(request_id)s | %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
