"""HTTP API (FastAPI): knowledge bases, config versions, builds, chat (SSE), traces, evaluation, system.

Streams use Server-Sent Events over ``POST``/``GET`` responses so they work with ``fetch`` and auth headers.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import statistics
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from ragkit import __version__
from ragkit.agent import Agent, ChatRequest, suggest_questions
from ragkit.cache import AnswerCache
from ragkit.config import (
    INDEX_STAGES,
    PRESET_DESCRIPTIONS,
    PRESETS,
    KnowledgeBaseConfig,
    config_json_schema,
    deep_merge,
    preset_config,
)
from ragkit.evaluation import GoldenItem, evaluate
from ragkit.index import BUILTIN_FILTER_FIELDS, VectorIndex
from ragkit.jobs import BUILD_STAGES, Builder, is_terminal, run_worker
from ragkit.memory import SessionMemory
from ragkit.observability import (
    UNMATCHED_ROUTE,
    Metrics,
    SloTargets,
    correlation_id,
    evaluate_slos,
    traceparent,
)
from ragkit.providers import ProviderError, ProviderRegistry
from ragkit.settings import Settings
from ragkit.sources import SourceError, discover, resolve_root
from ragkit.storage import Database, JobConflictError
from ragkit.telemetry import request_id_var

logger = logging.getLogger(__name__)
access_logger = logging.getLogger("ragkit.access")

NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$"


@dataclass
class AppContext:
    settings: Settings
    db: Database
    index: VectorIndex
    providers: ProviderRegistry
    agent: Agent
    builder: Builder
    memory: SessionMemory
    metrics: Metrics
    worker_task: asyncio.Task[None] | None = None
    worker_stop: asyncio.Event = field(default_factory=asyncio.Event)


# Request bodies


class CreateKnowledgeBase(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)
    preset: str = "balanced"
    config: dict[str, Any] = Field(default_factory=dict)


class ConfigBody(BaseModel):
    config: dict[str, Any]


class ChatBody(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=100)
    filters: dict[str, str | int | list[str]] = Field(default_factory=dict, max_length=10)
    profile: dict[str, Annotated[str, Field(max_length=300)]] = Field(default_factory=dict, max_length=5)


class EvalBody(BaseModel):
    version: int | None = None
    items: list[GoldenItem] = Field(min_length=1, max_length=500)


def _context(request: Request) -> AppContext:
    context: AppContext = request.app.state.context
    return context


Ctx = Annotated[AppContext, Depends(_context)]


def create_app(
    settings: Settings, *, providers: ProviderRegistry | None = None, in_process_worker: bool = False
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings.db_path)
        index = VectorIndex(settings)
        registry = providers or ProviderRegistry(settings)
        memory = SessionMemory()
        metrics = Metrics(db, settings.qdrant_mode)
        context = AppContext(
            settings=settings,
            db=db,
            index=index,
            providers=registry,
            memory=memory,
            metrics=metrics,
            agent=Agent(db, index, registry, AnswerCache(db), memory, observer=metrics.observe_trace),
            builder=Builder(db, index, registry, settings),
        )
        if in_process_worker:
            context.worker_task = asyncio.create_task(run_worker(context.builder, stop=context.worker_stop))
        app.state.context = context
        try:
            yield
        finally:
            context.worker_stop.set()
            if context.worker_task:
                await context.worker_task
            await registry.close()
            index.close()
            db.close()

    app = FastAPI(title="ragkit", version=__version__, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def require_api_key(request: Request, call_next: Any) -> Any:
        path = request.url.path
        if (
            settings.api_key
            and (path.startswith("/api/") or path == "/metrics")
            and path != "/api/system/health"
            and request.method != "OPTIONS"
        ):
            supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(supplied.encode(), settings.api_key.encode()):
                return JSONResponse({"detail": "missing or invalid API key"}, status_code=401)
        return await call_next(request)

    # Outermost middleware (added last): correlation id, access log and HTTP metrics for every request, 401s included.
    @app.middleware("http")
    async def correlate(request: Request, call_next: Any) -> Any:
        request_id = correlation_id(request.headers)
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            if parent := traceparent(request_id):
                response.headers["traceparent"] = parent
            return response
        finally:
            seconds = time.perf_counter() - started
            route = getattr(request.scope.get("route"), "path", None) or UNMATCHED_ROUTE
            context = getattr(request.app.state, "context", None)
            if context is not None and route != "/metrics":
                context.metrics.observe_http(request.method, route, status, seconds)
            access_logger.info("%s %s %d %.1fms", request.method, route, status, seconds * 1000)
            request_id_var.reset(token)

    @app.exception_handler(ProviderError)
    async def provider_error(_: Request, exc: ProviderError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.exception_handler(SourceError)
    async def source_error(_: Request, exc: SourceError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    def kb_or_404(context: AppContext, kb_id: str) -> dict[str, Any]:
        kb = context.db.get_kb(kb_id)
        if kb is None:
            raise HTTPException(404, "knowledge base not found")
        return kb

    def describe(context: AppContext, kb: dict[str, Any]) -> dict[str, Any]:
        active = context.db.active_job(kb["id"])
        jobs = context.db.list_jobs(kb["id"], limit=1)
        if active:
            status = "building"
        elif kb["active_collection"]:
            status = "ready" if kb["built_index_hash"] == kb["active_index_hash"] else "stale"
        elif jobs and jobs[0]["status"] == "failed":
            status = "failed"
        else:
            status = "draft"
        return {
            "id": kb["id"],
            "name": kb["name"],
            "status": status,
            "created_at": kb["created_at"],
            "active_version": kb["active_version"],
            "built_version": kb["built_version"],
            "documents": kb["document_count"],
            "chunks": kb["chunk_count"],
            "active_job": active,
            "last_job": jobs[0] if jobs else None,
        }

    def parse_config(raw: dict[str, Any]) -> KnowledgeBaseConfig:
        try:
            return KnowledgeBaseConfig.model_validate(raw)
        except ValidationError as exc:
            raise HTTPException(422, json.loads(exc.json(include_url=False))) from exc

    # System

    @app.get("/api/system/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/system/ready")
    async def ready(context: Ctx) -> dict[str, Any]:
        qdrant = await asyncio.to_thread(context.index.ping)
        ollama = await context.providers.get("ollama").ping()
        return {"ready": qdrant, "qdrant": qdrant, "ollama": ollama}

    @app.get("/api/system/capabilities")
    async def capabilities(context: Ctx) -> dict[str, Any]:
        return {
            "qdrant": context.index.capabilities(),
            "rerank_provider": context.settings.rerank_provider,
            "auth": bool(context.settings.api_key),
            "data_roots": [str(p) for p in context.settings.data_roots],
        }

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request, context: Ctx) -> Response:
        body, content_type = await asyncio.to_thread(context.metrics.render, request.headers.get("accept", ""))
        return Response(body, media_type=content_type)

    @app.get("/api/slo")
    async def slo(context: Ctx, kb_id: str | None = None) -> dict[str, Any]:
        targets = SloTargets(
            availability=context.settings.slo_availability,
            latency_target=context.settings.slo_latency_target,
            latency_seconds=context.settings.slo_latency_seconds,
            window_days=context.settings.slo_window_days,
        )
        since = time.time() - max(targets.window_days * 86400, 3 * 86400)
        report = evaluate_slos(context.db.slo_rows(kb_id, since), targets)
        recent = time.time() - 86400
        for kb in context.db.list_kbs():
            jobs = context.db.list_jobs(kb["id"], limit=1) if kb_id in (None, kb["id"]) else []
            if jobs and jobs[0]["status"] == "failed" and (jobs[0]["finished_at"] or 0) > recent:
                alert = {"name": "build_failed", "slo": None, "severity": "ticket", "state": "firing"}
                report["alerts"].append(
                    alert | {"kb_id": kb["id"], "job_id": jobs[0]["id"], "detail": jobs[0]["error"]}
                )
        return report

    @app.get("/api/schema")
    async def schema() -> dict[str, Any]:
        return {
            "schema": config_json_schema(),
            "index_stages": list(INDEX_STAGES),
            "presets": [{"name": name, "description": PRESET_DESCRIPTIONS[name]} for name in PRESETS],
        }

    @app.get("/api/presets/{name}")
    async def preset(name: str) -> dict[str, Any]:
        if name not in PRESETS:
            raise HTTPException(404, "unknown preset")
        return preset_config(name).model_dump()

    @app.get("/api/models")
    async def models(context: Ctx, provider: Literal["ollama", "openai"] = "ollama") -> dict[str, Any]:
        return {"provider": provider, "models": await context.providers.get(provider).list_models()}

    # Knowledge bases and configs

    @app.get("/api/kbs")
    async def list_kbs(context: Ctx) -> list[dict[str, Any]]:
        return [describe(context, kb) for kb in context.db.list_kbs()]

    @app.post("/api/kbs", status_code=201)
    async def create_kb(body: CreateKnowledgeBase, context: Ctx) -> dict[str, Any]:
        if body.preset not in PRESETS:
            raise HTTPException(422, f"unknown preset '{body.preset}'")
        if context.db.get_kb_by_name(body.name):
            raise HTTPException(409, "a knowledge base with this name exists")
        config = parse_config(deep_merge(preset_config(body.preset).model_dump(), body.config))
        kb_id = context.db.create_kb(body.name, config)
        return describe(context, kb_or_404(context, kb_id))

    @app.get("/api/kbs/{kb_id}")
    async def get_kb(kb_id: str, context: Ctx) -> dict[str, Any]:
        kb = kb_or_404(context, kb_id)
        _, config = context.db.get_config(kb_id)
        return describe(context, kb) | {"config": config.model_dump()}

    @app.delete("/api/kbs/{kb_id}", status_code=204)
    async def delete_kb(kb_id: str, context: Ctx) -> None:
        kb_or_404(context, kb_id)
        if context.db.active_job(kb_id):
            raise HTTPException(409, "cancel the running build first")
        for name in await asyncio.to_thread(context.index.drop_prefix, f"{kb_id}__"):
            context.db.forget_documents(name)
        context.db.delete_kb(kb_id)

    @app.get("/api/kbs/{kb_id}/configs")
    async def list_configs(kb_id: str, context: Ctx) -> list[dict[str, Any]]:
        kb_or_404(context, kb_id)
        return context.db.list_versions(kb_id)

    @app.get("/api/kbs/{kb_id}/configs/{version}")
    async def get_config(kb_id: str, version: int, context: Ctx) -> dict[str, Any]:
        kb_or_404(context, kb_id)
        try:
            _, config = context.db.get_config(kb_id, version)
        except KeyError as exc:
            raise HTTPException(404, "version not found") from exc
        return config.model_dump()

    @app.post("/api/kbs/{kb_id}/configs", status_code=201)
    async def save_config(kb_id: str, body: ConfigBody, context: Ctx) -> dict[str, Any]:
        kb = kb_or_404(context, kb_id)
        config = parse_config(body.config)
        version = context.db.add_config_version(kb_id, config)
        requires_rebuild = config.index_fingerprint() != kb["built_index_hash"]
        return {"version": version, "requires_rebuild": requires_rebuild}

    @app.post("/api/kbs/{kb_id}/configs/validate")
    async def validate_config(kb_id: str, body: ConfigBody, context: Ctx) -> dict[str, Any]:
        kb = kb_or_404(context, kb_id)
        try:
            config = KnowledgeBaseConfig.model_validate(body.config)
        except ValidationError as exc:
            return {"valid": False, "errors": json.loads(exc.json(include_url=False)), "warnings": [], "checks": {}}
        return await _validate(context, kb, config)

    # Builds

    @app.post("/api/kbs/{kb_id}/builds", status_code=202)
    async def start_build(kb_id: str, context: Ctx) -> dict[str, Any]:
        kb = kb_or_404(context, kb_id)
        try:
            job_id = context.db.create_job(kb_id, kb["active_version"], BUILD_STAGES)
        except JobConflictError as exc:
            raise HTTPException(409, f"build {exc.job_id} is already running") from exc
        return {"job_id": job_id}

    @app.get("/api/kbs/{kb_id}/builds")
    async def list_builds(kb_id: str, context: Ctx) -> list[dict[str, Any]]:
        kb_or_404(context, kb_id)
        return context.db.list_jobs(kb_id)

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str, context: Ctx) -> dict[str, Any]:
        job = context.db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return job

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str, context: Ctx) -> dict[str, bool]:
        if context.db.get_job(job_id) is None:
            raise HTTPException(404, "job not found")
        return {"cancel_requested": context.db.request_cancel(job_id)}

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, context: Ctx) -> StreamingResponse:
        if context.db.get_job(job_id) is None:
            raise HTTPException(404, "job not found")

        async def stream() -> AsyncIterator[str]:
            last = None
            while True:
                job = context.db.get_job(job_id)
                if job is None:
                    return
                snapshot = json.dumps(job, default=str)
                if snapshot != last:
                    last = snapshot
                    yield _sse("job", snapshot)
                if is_terminal(job["status"]):
                    return
                await asyncio.sleep(0.5)

        return _sse_response(stream())

    @app.post("/api/kbs/{kb_id}/uploads", status_code=201)
    async def upload(kb_id: str, context: Ctx, files: Annotated[list[UploadFile], File()]) -> dict[str, Any]:
        kb_or_404(context, kb_id)
        _, config = context.db.get_config(kb_id)
        if config.source.type != "uploads":
            raise HTTPException(409, "set the source type to 'uploads' to add files")
        target = resolve_root(config.source, context.settings, kb_id)
        limit = context.settings.max_upload_mb * 1024 * 1024
        saved = []
        for item in files:
            name = Path(item.filename or "").name
            if not name or Path(name).suffix.lower() not in config.source.file_types:
                raise HTTPException(422, f"'{name}' is not an allowed file type")
            size = 0
            destination = target / name
            with destination.open("wb") as handle:
                while block := await item.read(1 << 20):
                    size += len(block)
                    if size > limit:
                        handle.close()
                        destination.unlink(missing_ok=True)
                        raise HTTPException(413, f"'{name}' exceeds {context.settings.max_upload_mb} MB")
                    handle.write(block)
            saved.append({"name": name, "bytes": size})
        return {"saved": saved}

    @app.get("/api/kbs/{kb_id}/suggestions")
    async def suggestions(kb_id: str, context: Ctx) -> list[str]:
        kb = kb_or_404(context, kb_id)
        if not kb["active_collection"]:
            return []
        payloads = await asyncio.to_thread(context.index.sample_payloads, kb["active_collection"], 64)
        return suggest_questions(payloads)

    @app.get("/api/kbs/{kb_id}/uploads")
    async def list_uploads(kb_id: str, context: Ctx) -> list[dict[str, Any]]:
        kb_or_404(context, kb_id)
        folder = context.settings.uploads_dir / kb_id
        return (
            [{"name": p.name, "bytes": p.stat().st_size} for p in sorted(folder.glob("*")) if p.is_file()]
            if folder.is_dir()
            else []
        )

    # Chat

    @app.post("/api/kbs/{kb_id}/chat")
    async def chat(kb_id: str, body: ChatBody, context: Ctx) -> StreamingResponse:
        kb_or_404(context, kb_id)
        _, config = context.db.get_config(kb_id)
        allowed = {*BUILTIN_FILTER_FIELDS, *config.retrieval.filter_fields}
        if unknown := set(body.filters) - allowed:
            raise HTTPException(422, f"cannot filter on {sorted(unknown)}; allowed: {sorted(allowed)}")
        request = ChatRequest(body.query, body.session_id, dict(body.filters), dict(body.profile))

        async def stream() -> AsyncIterator[str]:
            async for event in context.agent.run(kb_id, request):
                yield _sse(event["type"], json.dumps(event))

        return _sse_response(stream())

    @app.delete("/api/kbs/{kb_id}/sessions/{session_id}", status_code=204)
    async def clear_session(kb_id: str, session_id: str, context: Ctx) -> None:
        context.memory.clear(session_id)

    # Telemetry and evaluation

    @app.get("/api/traces")
    async def traces(
        context: Ctx, kb_id: str | None = None, outcome: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        return context.db.list_traces(kb_id, outcome, max(1, min(limit, 500)), max(0, offset))

    @app.get("/api/traces/{trace_id}")
    async def trace(trace_id: str, context: Ctx) -> dict[str, Any]:
        record = context.db.get_trace(trace_id)
        if record is None:
            raise HTTPException(404, "trace not found")
        return record

    @app.get("/api/kbs/{kb_id}/stats")
    async def stats(kb_id: str, context: Ctx) -> dict[str, Any]:
        kb_or_404(context, kb_id)
        return summarize_traces(context.db.trace_metrics(kb_id))

    @app.post("/api/kbs/{kb_id}/eval")
    async def run_eval(kb_id: str, body: EvalBody, context: Ctx) -> dict[str, Any]:
        kb_or_404(context, kb_id)
        try:
            return await evaluate(context.db, context.index, context.providers, kb_id, body.items, body.version)
        except (ValueError, KeyError) as exc:
            raise HTTPException(409, str(exc)) from exc

    if settings.ui_dist_dir and (settings.ui_dist_dir / "index.html").is_file():
        _mount_ui(app, settings.ui_dist_dir)
    return app


async def _validate(context: AppContext, kb: dict[str, Any], config: KnowledgeBaseConfig) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    try:
        refs = await asyncio.to_thread(discover, config.source, context.settings, kb["id"])
        by_type: dict[str, int] = {}
        for ref in refs:
            by_type[ref.path.suffix.lower()] = by_type.get(ref.path.suffix.lower(), 0) + 1
        checks["source"] = {
            "ok": bool(refs),
            "files": len(refs),
            "by_type": by_type,
            "megabytes": round(sum(r.size for r in refs) / 1e6, 2),
        }
        if not refs:
            warnings.append({"stage": "source", "msg": "This folder has no supported files yet."})
    except SourceError as exc:
        checks["source"] = {"ok": False}
        errors.append({"loc": ["source", "path"], "msg": str(exc)})

    for stage_name in ("embedding", "generation"):
        stage = getattr(config, stage_name)
        try:
            available = await context.providers.get(stage.provider).list_models()
            found = any(m == stage.model or m.startswith(stage.model + ":") for m in available)
            checks[stage_name] = {"ok": found, "model": stage.model}
            if not found:
                errors.append(
                    {"loc": [stage_name, "model"], "msg": f"'{stage.model}' is not available at {stage.provider}"}
                )
        except Exception as exc:
            checks[stage_name] = {"ok": False, "model": stage.model}
            errors.append({"loc": [stage_name, "provider"], "msg": f"{stage.provider} unreachable: {exc}"})

    if not context.index.capabilities()["hnsw"]:
        warnings.append(
            {
                "stage": "index",
                "msg": "Built-in search mode scans every passage (brute force), so the speed settings below "
                "only apply with a Qdrant server.",
            }
        )
    if config.rerank.enabled and context.settings.rerank_provider == "none":
        warnings.append(
            {
                "stage": "rerank",
                "msg": "Re-scoring is on, but the server has it switched off (RAG_RERANK_PROVIDER=none).",
            }
        )
    requires_rebuild = config.index_fingerprint() != kb["built_index_hash"]
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "requires_rebuild": requires_rebuild,
    }


def summarize_traces(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"requests": 0}
    totals = sorted(row["total_ms"] for row in rows)
    first_tokens = sorted(row["ttft_ms"] for row in rows if row["ttft_ms"] is not None)

    def rate(predicate: Any) -> float:
        return round(sum(1 for row in rows if predicate(row)) / len(rows), 4)

    return {
        "requests": len(rows),
        "latency_p50_ms": round(statistics.median(totals), 1),
        "latency_p95_ms": round(totals[int(0.95 * (len(totals) - 1))], 1),
        "ttft_p50_ms": round(statistics.median(first_tokens), 1) if first_tokens else None,
        "cache_hit_rate": rate(lambda r: r["cache_hit"]),
        "abstain_rate": rate(lambda r: r["outcome"] == "abstained"),
        "error_rate": rate(lambda r: r["outcome"] == "error"),
        "verify_fail_rate": rate(lambda r: r["verify"] == "fail"),
    }


def _sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


def _sse_response(stream: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        stream, media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


def _mount_ui(app: FastAPI, dist: Path) -> None:
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    index_html = dist / "index.html"
    safe_name = re.compile(r"^[\w.-]+$")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404)
        candidate = dist / path
        if safe_name.match(path) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_html)
