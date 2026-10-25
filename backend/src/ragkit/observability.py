"""Correlation, metrics and SLOs: the operational half of telemetry (traces live in ``telemetry.py``).

Correlation: every HTTP request gets one id: the incoming W3C ``traceparent`` trace id, else a well-formed
``X-Request-ID``, else a fresh one. It is the chat trace's id, appears on every log line, in the response
headers, in metric exemplars and in the UI's trace links, and is forwarded to model servers.

Metrics: Prometheus series with a fixed label budget. Labels only take values from closed sets (route templates,
status classes, outcomes, verdicts, stages); knowledge-base ids, session ids, raw paths and queries never become
labels. Per-chatbot numbers come from the trace store instead.

SLOs: availability and latency objectives evaluated from stored traces with multi-window burn rates, so a local
install without Prometheus still sees its error budget and alerts; ``deploy/observability`` has the same rules
for Prometheus.
"""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.exposition import generate_latest
from prometheus_client.openmetrics.exposition import generate_latest as generate_openmetrics

from ragkit import __version__
from ragkit.storage import Database
from ragkit.telemetry import Trace, request_id_var

__all__ = ["Metrics", "SloTargets", "correlation_id", "evaluate_slos", "request_id_var", "traceparent"]

_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")  # validated: it is echoed into headers and logs

OUTCOMES = ("answered", "abstained", "cache_hit", "error")
VERDICTS = ("pass", "fail", "skipped", "none")
ERROR_STAGES = ("prepare", "condense", "embed", "decompose", "retrieve", "reformulate", "generate", "verify", "other")
UNMATCHED_ROUTE = "unmatched"


def correlation_id(headers: Mapping[str, str]) -> str:
    match = _TRACEPARENT.match(headers.get("traceparent", "").strip().lower())
    if match and match.group(1) != "0" * 32:
        return match.group(1)
    supplied = headers.get("x-request-id", "").strip()
    return supplied if _REQUEST_ID.match(supplied) else secrets.token_hex(16)


def traceparent(trace_id: str) -> str | None:
    """A W3C traceparent continuing ``trace_id`` with a new span id, when the id is a W3C trace id."""
    return f"00-{trace_id}-{secrets.token_hex(8)}-01" if re.fullmatch(r"[0-9a-f]{32}", trace_id) else None


def status_class(status: int) -> str:
    return f"{status // 100}xx"


def error_stage(stage: str | None) -> str:
    base = (stage or "other").split(":", 1)[0]
    return base if base in ERROR_STAGES else "other"


class Metrics:
    """Prometheus series for one app instance (own registry, so tests and reloads never collide)."""

    def __init__(self, db: Database | None = None, qdrant_mode: str = "embedded") -> None:
        self.registry = CollectorRegistry()
        seconds = (0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120)
        self.http_requests = Counter(
            "ragkit_http_requests", "HTTP requests.", ["method", "route", "status_class"], registry=self.registry
        )
        self.http_duration = Histogram(
            "ragkit_http_request_duration_seconds",
            "Time to response headers (streams are measured by the chat series).",
            ["method", "route"],
            buckets=seconds,
            registry=self.registry,
        )
        self.chat_requests = Counter(
            "ragkit_chat_requests", "Chat requests by outcome.", ["outcome"], registry=self.registry
        )
        self.chat_duration = Histogram(
            "ragkit_chat_duration_seconds",
            "Full chat request duration.",
            ["outcome"],
            buckets=seconds,
            registry=self.registry,
        )
        self.first_output = Histogram(
            "ragkit_chat_first_output_seconds",
            "Time until the user sees output: first token, or the whole reply for cache hits and abstentions.",
            buckets=seconds,
            registry=self.registry,
        )
        self.confidence = Histogram(
            "ragkit_retrieval_confidence",
            "Retrieval confidence of answered requests.",
            buckets=(0.1, 0.2, 0.35, 0.5, 0.65, 0.8, 0.9, 1.0),
            registry=self.registry,
        )
        self.retries = Counter("ragkit_agent_retries", "Retrieval reformulations.", registry=self.registry)
        self.verify = Counter(
            "ragkit_answer_verification", "Answer checks by verdict.", ["verdict"], registry=self.registry
        )
        self.errors = Counter("ragkit_chat_errors", "Failed chat requests by stage.", ["stage"], registry=self.registry)
        self.tokens = Counter("ragkit_llm_tokens", "LLM tokens.", ["kind"], registry=self.registry)
        info = Gauge("ragkit_build_info", "Build information.", ["version", "qdrant_mode"], registry=self.registry)
        info.labels(__version__, qdrant_mode).set(1)
        for outcome in OUTCOMES:  # pre-create so rates exist before the first request
            self.chat_requests.labels(outcome)
        if db is not None:
            self.registry.register(_StoreCollector(db))

    def observe_http(self, method: str, route: str, status: int, seconds: float) -> None:
        self.http_requests.labels(method, route, status_class(status)).inc()
        self.http_duration.labels(method, route).observe(seconds)

    def observe_trace(self, trace: Trace) -> None:
        outcome = trace.outcome if trace.outcome in OUTCOMES else "error"
        exemplar = {"trace_id": trace.id}
        total = trace.elapsed_ms() / 1000
        self.chat_requests.labels(outcome).inc(exemplar=exemplar)
        self.chat_duration.labels(outcome).observe(total, exemplar=exemplar)
        if outcome != "error":
            first = trace.ttft_ms / 1000 if trace.ttft_ms is not None else total
            self.first_output.observe(first, exemplar=exemplar)
        if outcome == "answered" and trace.confidence is not None:
            self.confidence.observe(trace.confidence)
        if trace.retries:
            self.retries.inc(trace.retries)
        self.verify.labels(trace.verify if trace.verify in VERDICTS else "none").inc()
        if outcome == "error":
            self.errors.labels(error_stage((trace.error or {}).get("stage"))).inc()
        for kind in ("prompt", "completion"):
            if trace.tokens.get(kind):
                self.tokens.labels(kind).inc(trace.tokens[kind])

    def render(self, accept: str) -> tuple[bytes, str]:
        if "application/openmetrics-text" in accept:  # carries exemplars (trace ids)
            return generate_openmetrics(self.registry), "application/openmetrics-text; version=1.0.0; charset=utf-8"
        return generate_latest(self.registry), "text/plain; version=0.0.4; charset=utf-8"


class _StoreCollector:
    """Gauges read from the database at scrape time, so build activity from a separate worker process counts."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def collect(self) -> Iterable[GaugeMetricFamily]:
        jobs = GaugeMetricFamily("ragkit_build_jobs", "Build jobs by status (all time).", labels=["status"])
        for status, count in self.db.job_status_counts().items():
            jobs.add_metric([status], count)
        kbs = self.db.list_kbs()
        chatbots = GaugeMetricFamily("ragkit_chatbots", "Chatbots (knowledge bases).", value=len(kbs))
        chunks = GaugeMetricFamily(
            "ragkit_indexed_chunks", "Chunks in active indexes.", value=sum(int(kb["chunk_count"] or 0) for kb in kbs)
        )
        failed = GaugeMetricFamily(
            "ragkit_build_last_failure_timestamp_seconds",
            "Finish time of the most recent failed build (0 = never).",
            value=self.db.last_failed_build_time() or 0,
        )
        return [jobs, chatbots, chunks, failed]

    def describe(self) -> list[Any]:
        return []  # dynamic families: tell the registry not to collect at registration time


# SLOs


@dataclass(frozen=True)
class SloTargets:
    availability: float = 0.99  # share of chat requests that do not error
    latency_target: float = 0.95  # share of successful requests whose first output arrives within ...
    latency_seconds: float = 5.0  # ... this many seconds
    window_days: int = 7


# Multi-window, multi-burn-rate alerting (Google SRE workbook): long window, short window, burn threshold.
BURN_ALERTS = (
    ("page", 3600, 300, 14.4),
    ("page", 6 * 3600, 1800, 6.0),
    ("ticket", 3 * 86400, 6 * 3600, 1.0),
)
MIN_REQUESTS_FOR_ALERT = 20  # below this, one bad request reads as a 100 % failure rate


def evaluate_slos(rows: list[dict[str, Any]], targets: SloTargets, now: float | None = None) -> dict[str, Any]:
    """SLO status and burn-rate alerts from trace rows (created_at, outcome, total_ms, ttft_ms)."""
    now = time.time() if now is None else now
    window_start = now - targets.window_days * 86400
    in_window = [r for r in rows if r["created_at"] >= window_start]

    def availability_bad(row: dict[str, Any]) -> bool | None:
        return row["outcome"] == "error"

    def latency_bad(row: dict[str, Any]) -> bool | None:
        if row["outcome"] == "error":
            return None  # counted by availability, excluded from latency
        first = row["ttft_ms"] if row["ttft_ms"] is not None else row["total_ms"]
        return first > targets.latency_seconds * 1000

    objectives = [
        (
            "availability",
            f"{targets.availability:.2%} of chat requests succeed",
            targets.availability,
            availability_bad,
        ),
        (
            "latency",
            f"{targets.latency_target:.0%} of answers start within {targets.latency_seconds:g}s",
            targets.latency_target,
            latency_bad,
        ),
    ]
    slos, alerts = [], []
    for name, description, target, is_bad in objectives:
        judged = [bad for bad in (is_bad(r) for r in in_window) if bad is not None]
        good_ratio = 1 - sum(judged) / len(judged) if judged else None
        budget = 1 - target
        remaining = None if good_ratio is None else max(-1.0, 1 - (1 - good_ratio) / budget)
        slos.append(
            {
                "name": name,
                "description": description,
                "target": target,
                "window_days": targets.window_days,
                "requests": len(judged),
                "sli": None if good_ratio is None else round(good_ratio, 5),
                "error_budget_remaining": None if remaining is None else round(remaining, 4),
                "met": None if good_ratio is None else good_ratio >= target,
            }
        )
        for severity, long_s, short_s, threshold in BURN_ALERTS:
            long_rate, long_count = _burn(rows, is_bad, now - long_s, budget)
            short_rate, _ = _burn(rows, is_bad, now - short_s, budget)
            firing = long_count >= MIN_REQUESTS_FOR_ALERT and long_rate >= threshold and short_rate >= threshold
            alerts.append(
                {
                    "name": f"{name}_burn_{_span(long_s)}",
                    "slo": name,
                    "severity": severity,
                    "state": "firing" if firing else "ok",
                    "burn_rate": round(long_rate, 2),
                    "threshold": threshold,
                    "window": f"{_span(long_s)}/{_span(short_s)}",
                    "requests": long_count,
                }
            )
    return {"slos": slos, "alerts": alerts}


def _burn(rows: list[dict[str, Any]], is_bad: Any, since: float, budget: float) -> tuple[float, int]:
    judged = [bad for bad in (is_bad(r) for r in rows if r["created_at"] >= since) if bad is not None]
    if not judged:
        return 0.0, 0
    return (sum(judged) / len(judged)) / budget, len(judged)


def _span(seconds: int) -> str:
    return (
        f"{seconds // 86400}d"
        if seconds >= 86400
        else f"{seconds // 3600}h"
        if seconds >= 3600
        else f"{seconds // 60}m"
    )
