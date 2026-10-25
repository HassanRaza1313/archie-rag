"""The seven observability concerns: logs, metrics, traces, correlation, alerts, SLOs and cardinality."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from ragkit.api import create_app
from ragkit.observability import (
    MIN_REQUESTS_FOR_ALERT,
    Metrics,
    SloTargets,
    correlation_id,
    error_stage,
    evaluate_slos,
    traceparent,
)
from ragkit.providers import _forward_request_id
from ragkit.settings import Settings
from ragkit.telemetry import CorrelationFilter, Trace, request_id_var
from tests.conftest import FakeRegistry
from tests.test_api import build, create_kb, sse_events

RULES = Path(__file__).resolve().parents[2] / "deploy" / "observability" / "rules.yml"
QUESTION = "What ratio of browns to greens should a compost pile have?"


@pytest.fixture
def client(settings: Settings, registry: FakeRegistry) -> Iterator[TestClient]:
    with TestClient(create_app(settings, providers=registry, in_process_worker=True)) as test_client:  # type: ignore[arg-type]
        yield test_client


def built_kb(client: TestClient, corpus: Path) -> str:
    kb = create_kb(client, corpus)
    assert build(client, kb["id"])["status"] == "completed"
    return str(kb["id"])


# Correlation


def test_correlation_id_sources_and_validation() -> None:
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    assert correlation_id({"traceparent": f"00-{trace_id}-00f067aa0ba902b7-01"}) == trace_id
    assert correlation_id({"traceparent": "00-" + "0" * 32 + "-00f067aa0ba902b7-01"}) != "0" * 32
    assert correlation_id({"x-request-id": "client-req-123"}) == "client-req-123"
    for hostile in ("short", "a" * 65, "bad id\nInjected: header", "id;rm -rf"):
        generated = correlation_id({"x-request-id": hostile})
        assert generated != hostile and re.fullmatch(r"[0-9a-f]{32}", generated)
    assert traceparent(trace_id).startswith(f"00-{trace_id}-")  # type: ignore[union-attr]
    assert traceparent("client-req-123") is None


def test_one_id_links_header_trace_logs_and_metrics(
    client: TestClient, corpus: Path, caplog: pytest.LogCaptureFixture
) -> None:
    kb_id = built_kb(client, corpus)
    caplog.handler.addFilter(CorrelationFilter())
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    with caplog.at_level(logging.INFO):
        response = client.post(
            f"/api/kbs/{kb_id}/chat",
            json={"query": QUESTION},
            headers={"traceparent": f"00-{trace_id}-00f067aa0ba902b7-01"},
        )
    assert response.headers["x-request-id"] == trace_id
    assert response.headers["traceparent"].startswith(f"00-{trace_id}-")
    done = sse_events(response.text)[-1]
    assert done["trace_id"] == trace_id
    assert client.get(f"/api/traces/{trace_id}").status_code == 200
    request_lines = [r for r in caplog.records if r.name == "ragkit.request"]
    access_lines = [r for r in caplog.records if r.name == "ragkit.access" and "/chat" in r.getMessage()]
    assert request_lines and all(r.request_id == trace_id for r in request_lines)  # type: ignore[attr-defined]
    assert access_lines and access_lines[0].request_id == trace_id  # type: ignore[attr-defined]
    exposition = client.get("/metrics", headers={"accept": "application/openmetrics-text"}).text
    assert f'trace_id="{trace_id}"' in exposition  # exemplar: metric -> trace


def test_every_response_carries_a_request_id(client: TestClient) -> None:
    first = client.get("/api/system/health").headers["x-request-id"]
    second = client.get("/api/system/health").headers["x-request-id"]
    assert first != second and re.fullmatch(r"[0-9a-f]{32}", first)
    assert (
        client.get("/api/system/health", headers={"x-request-id": "my-request-0001"}).headers["x-request-id"]
        == "my-request-0001"
    )


async def test_request_id_is_forwarded_to_model_servers() -> None:
    request = httpx.Request("POST", "http://localhost:11434/api/chat")
    token = request_id_var.set("abc123def456")
    try:
        await _forward_request_id(request)
    finally:
        request_id_var.reset(token)
    assert request.headers["x-request-id"] == "abc123def456"
    bare = httpx.Request("GET", "http://localhost:11434/api/tags")
    await _forward_request_id(bare)
    assert "x-request-id" not in bare.headers


def test_build_logs_carry_the_job_id(
    settings: Settings, registry: FakeRegistry, corpus: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.handler.addFilter(CorrelationFilter())
    with (
        caplog.at_level(logging.INFO),
        TestClient(
            create_app(settings, providers=registry, in_process_worker=True)  # type: ignore[arg-type]
        ) as client,
    ):
        kb = create_kb(client, corpus)
        job = build(client, kb["id"])
    lines = [r for r in caplog.records if r.name == "ragkit.jobs" and "build" in r.getMessage()]
    assert lines and {r.request_id for r in lines} == {job["id"]}  # type: ignore[attr-defined]


# Logs


def test_json_logs_include_request_id(capsys: pytest.CaptureFixture[str]) -> None:
    from ragkit.telemetry import configure_logging

    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    try:
        configure_logging("INFO", "json")
        token = request_id_var.set("req-json-0001")
        logging.getLogger("ragkit.test").info("hello")
        request_id_var.reset(token)
        logging.getLogger("ragkit.test").info("outside")
    finally:
        root.handlers[:], level = saved
        root.setLevel(level)
    lines = [line for line in capsys.readouterr().out.splitlines() if '"logger": "ragkit.test"' in line]
    assert '"request_id": "req-json-0001"' in lines[0] and '"request_id": "-"' in lines[1]


# Metrics and cardinality


def test_metrics_cover_http_chat_and_builds(client: TestClient, corpus: Path) -> None:
    kb_id = built_kb(client, corpus)
    client.post(f"/api/kbs/{kb_id}/chat", json={"query": QUESTION})
    client.post(f"/api/kbs/{kb_id}/chat", json={"query": "Who won the football world cup in 1998?"})
    text = client.get("/metrics").text
    assert 'ragkit_chat_requests_total{outcome="answered"} 1.0' in text
    assert 'ragkit_chat_requests_total{outcome="abstained"} 1.0' in text
    assert 'ragkit_chat_requests_total{outcome="error"} 0.0' in text
    assert 'ragkit_http_requests_total{method="POST",route="/api/kbs/{kb_id}/chat",status_class="2xx"} 2.0' in text
    assert "ragkit_chat_first_output_seconds_count 2.0" in text
    assert 'ragkit_answer_verification_total{verdict="pass"} 1.0' in text
    assert 'ragkit_build_jobs{status="completed"} 1.0' in text
    assert "ragkit_chatbots 1.0" in text and "ragkit_indexed_chunks" in text
    assert 'ragkit_build_info{qdrant_mode="embedded"' in text


def test_label_cardinality_is_bounded(client: TestClient, corpus: Path) -> None:
    kb_id = built_kb(client, corpus)
    for index in range(25):
        client.get(f"/api/kbs/kb_unknown_{index}")  # 404s on a templated route
        client.get(f"/api/no-such-endpoint/{index}")  # unmatched paths
        client.post(f"/api/kbs/{kb_id}/chat", json={"query": f"question {index}", "session_id": f"s{index}"})
    text = client.get("/metrics").text
    assert kb_id not in text and "kb_unknown" not in text and "no-such-endpoint" not in text
    assert "session" not in text and "question" not in text
    routes = set(re.findall(r'route="([^"]+)"', text))
    assert "unmatched" in routes and all(r == "unmatched" or r.startswith("/") for r in routes)
    assert len(routes) <= 12
    series = [line for line in text.splitlines() if line.startswith("ragkit_") and "{" in line]
    assert len(series) < 400  # a fixed budget, not one series per request, chatbot or session


def test_metrics_require_the_api_key_when_set(settings: Settings, registry: FakeRegistry) -> None:
    secured = settings.model_copy(update={"api_key": "s3cret"})
    with TestClient(create_app(secured, providers=registry)) as client:  # type: ignore[arg-type]
        assert client.get("/metrics").status_code == 401
        assert client.get("/metrics", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert 'status_class="4xx"' in client.get("/metrics", headers={"Authorization": "Bearer s3cret"}).text


def test_error_stage_label_is_closed() -> None:
    assert error_stage("reformulate:rewrite") == "reformulate"
    assert error_stage("generate") == "generate"
    assert error_stage("something-new") == "other" and error_stage(None) == "other"


def test_observe_trace_records_failures() -> None:
    metrics = Metrics()
    trace = Trace("kb", 1, "q")
    trace.outcome, trace.error = "error", {"stage": "generate", "type": "ProviderError", "message": "down"}
    metrics.observe_trace(trace)
    text = metrics.render("")[0].decode()
    assert 'ragkit_chat_errors_total{stage="generate"} 1.0' in text
    assert "ragkit_chat_first_output_seconds_count 0.0" in text  # errors are not latency samples


# SLOs and alerts


def rows(
    count: int, *, ago: float, error: bool = False, first_ms: float = 500, now: float = 1_000_000.0
) -> list[dict[str, Any]]:
    return [
        {
            "created_at": now - ago,
            "outcome": "error" if error else "answered",
            "total_ms": first_ms,
            "ttft_ms": first_ms,
        }
        for _ in range(count)
    ]


def test_slos_healthy() -> None:
    report = evaluate_slos(rows(100, ago=3600), SloTargets(), now=1_000_000.0)
    availability, latency = report["slos"]
    assert availability["sli"] == 1.0 and availability["met"] is True and availability["error_budget_remaining"] == 1.0
    assert latency["sli"] == 1.0
    assert all(alert["state"] == "ok" for alert in report["alerts"])


def test_fast_error_burn_pages() -> None:
    history = rows(200, ago=3000) + rows(50, ago=60, error=True)  # 20 % errors in the hour: burn 20
    report = evaluate_slos(history, SloTargets(), now=1_000_000.0)
    availability = report["slos"][0]
    assert availability["met"] is False and availability["error_budget_remaining"] < 0
    fast = next(a for a in report["alerts"] if a["name"] == "availability_burn_1h")
    assert fast["state"] == "firing" and fast["severity"] == "page" and fast["burn_rate"] >= 14.4


def test_old_errors_spend_budget_but_alerts_clear_once_burn_stops() -> None:
    history = rows(200, ago=60) + rows(30, ago=2 * 86400, error=True)
    report = evaluate_slos(history, SloTargets(), now=1_000_000.0)
    assert report["slos"][0]["met"] is False  # the 7-day budget is spent
    assert all(alert["state"] == "ok" for alert in report["alerts"])  # short windows are clean: nothing to page
    ongoing = history + rows(20, ago=600, error=True)
    burning = evaluate_slos(ongoing, SloTargets(), now=1_000_000.0)
    assert next(a for a in burning["alerts"] if a["name"] == "availability_burn_3d")["state"] == "firing"


def test_latency_slo_and_small_samples() -> None:
    slow = rows(40, ago=120, first_ms=9000)
    report = evaluate_slos(slow, SloTargets(latency_seconds=5), now=1_000_000.0)
    assert report["slos"][1]["sli"] == 0.0
    assert next(a for a in report["alerts"] if a["name"] == "latency_burn_1h")["state"] == "firing"
    few = evaluate_slos(rows(MIN_REQUESTS_FOR_ALERT - 1, ago=60, error=True), SloTargets(), now=1_000_000.0)
    assert all(alert["state"] == "ok" for alert in few["alerts"])  # one bad request is not an incident
    empty = evaluate_slos([], SloTargets(), now=1_000_000.0)
    assert empty["slos"][0]["sli"] is None and empty["slos"][0]["met"] is None


def test_slo_endpoint_and_build_failure_alert(client: TestClient, corpus: Path, tmp_path: Path) -> None:
    kb_id = built_kb(client, corpus)
    client.post(f"/api/kbs/{kb_id}/chat", json={"query": QUESTION})
    report = client.get("/api/slo", params={"kb_id": kb_id}).json()
    assert [slo["name"] for slo in report["slos"]] == ["availability", "latency"]
    assert report["slos"][0]["requests"] == 1 and report["slos"][0]["sli"] == 1.0
    empty = tmp_path / "data" / "empty"
    empty.mkdir()
    broken = create_kb(client, empty, name="broken")
    assert build(client, broken["id"])["status"] == "failed"
    alerts = client.get("/api/slo").json()["alerts"]
    failed = [a for a in alerts if a["name"] == "build_failed"]
    assert failed and failed[0]["kb_id"] == broken["id"] and failed[0]["state"] == "firing"


def test_prometheus_rules_reference_real_metrics() -> None:
    document = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    rules = [rule for group in document["groups"] for rule in group["rules"]]
    exposed = Metrics().render("")[0].decode() + "\nragkit_build_jobs\nup\n"
    names = set(re.findall(r"^(?:# TYPE )?([a-z_:]+)", exposed, re.MULTILINE))
    for rule in rules:
        assert ("record" in rule) != ("alert" in rule)
        for metric in re.findall(r"\b(ragkit_[a-z_]+)", rule["expr"]):
            assert any(name.startswith(metric) or metric.startswith(name) for name in names), metric
        if "alert" in rule:
            assert rule["labels"]["severity"] in {"page", "ticket"}
    records = {rule["record"] for rule in rules if "record" in rule}
    for rule in rules:
        for used in re.findall(r"\b(ragkit:[a-z_:0-9]+)", rule["expr"]):
            assert used in records, used
    alert_names = {rule.get("alert") for rule in rules}
    assert {"RagkitAvailabilityBudgetBurnFast", "RagkitLatencyBudgetBurnFast", "RagkitDown"} <= alert_names


def test_slo_settings_from_env() -> None:
    settings = Settings.from_env({"RAG_SLO_AVAILABILITY": "0.995", "RAG_SLO_LATENCY_SECONDS": "3"})
    assert settings.slo_availability == 0.995 and settings.slo_latency_seconds == 3.0
    with pytest.raises(ValueError):
        Settings.from_env({"RAG_SLO_AVAILABILITY": "1.5"})


def test_trace_id_defaults_to_current_request() -> None:
    token = request_id_var.set("feedfacefeedfacefeedfacefeedface")
    try:
        assert Trace("kb", 1, "q").id == "feedfacefeedfacefeedfacefeedface"
    finally:
        request_id_var.reset(token)
    assert re.fullmatch(r"[0-9a-f]{32}", Trace("kb", 1, "q").id)
    assert asyncio.iscoroutinefunction(_forward_request_id) and time.time() > 0
