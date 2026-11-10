"""HTTP API (FastAPI): knowledge bases, config versions, builds, chat (SSE), traces, evaluation, system.

Streams use Server-Sent Events over ``POST``/``GET`` responses so they work with ``fetch`` and auth headers.
"""

from __future__ import annotations

import asyncio
import hmac
import importlib.util
import ipaddress
import json
import logging
import re
import secrets
import shutil
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
from ragkit.accounts import (
    INVITE_DAYS,
    ROLES,
    SESSION_DAYS,
    Identity,
    check_password,
    hash_password,
    new_token,
    password_problem,
    rank,
    required_role,
    token_hash,
)
from ragkit.agent import Agent, ChatRequest, suggest_questions
from ragkit.cache import AnswerCache
from ragkit.chunking import embed_text_for
from ragkit.config import (
    INDEX_STAGES,
    PRESET_DESCRIPTIONS,
    PRESETS,
    KnowledgeBaseConfig,
    config_json_schema,
    deep_merge,
    preset_config,
)
from ragkit.enrichment import Enrichment
from ragkit.evaluation import GoldenItem, evaluate
from ragkit.formats import IMAGE_TYPES, MEDIA_TYPES
from ragkit.index import BUILTIN_FILTER_FIELDS, VectorIndex
from ragkit.jobs import BUILD_STAGES, Builder, is_terminal, run_worker
from ragkit.layout import layout_available
from ragkit.observability import (
    UNMATCHED_ROUTE,
    Metrics,
    SloTargets,
    correlation_id,
    evaluate_slos,
    traceparent,
)
from ragkit.parsing import LAYOUT_ONLY
from ragkit.providers import ProviderError, ProviderRegistry
from ragkit.settings import Settings
from ragkit.sources import SourceError, discover, list_folders, resolve_root
from ragkit.storage import Database, JobConflictError
from ragkit.telemetry import request_id_var
from ragkit.textutil import bm25_document_vector, estimate_tokens

logger = logging.getLogger(__name__)
access_logger = logging.getLogger("ragkit.access")

NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$"
SESSION_COOKIE = "archie_session"
# Checked when an email is unknown, so a wrong email takes as long as a wrong password.
_DUMMY_HASH = hash_password("not-a-real-password")
# Asking questions is not an administrative change, so it stays out of the audit log.
_NOT_AUDITED = re.compile(r"POST /api/kbs/[^/]+/(chat|eval|configs/validate)|DELETE /api/kbs/[^/]+/sessions/[^/]+")
LOCAL_EMBEDDING = ("fastembed", "BAAI/bge-small-en-v1.5")  # runs inside Archie: no model server needed


@dataclass
class AppContext:
    settings: Settings
    db: Database
    index: VectorIndex
    providers: ProviderRegistry
    agent: Agent
    builder: Builder
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


class SignInBody(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class OwnerBody(SignInBody):
    name: str = Field(default="", max_length=80)


class JoinBody(BaseModel):
    name: str = Field(default="", max_length=80)
    password: str = Field(max_length=200)


class PasswordBody(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


class InviteBody(BaseModel):
    email: str = Field(min_length=3, max_length=200, pattern=r"^[^@\s]+@[^@\s]+$")
    role: Literal["viewer", "editor", "owner"] = "viewer"


class UserChange(BaseModel):
    role: Literal["viewer", "editor", "owner"] | None = None
    disabled: bool | None = None


class ShareBody(BaseModel):
    enabled: bool
    reset: bool = False


class ChunkEditBody(BaseModel):
    uri: str = Field(min_length=1, max_length=2000)
    ordinal: int = Field(ge=0)
    text: str | None = Field(default=None, min_length=1, max_length=40_000)
    excluded: bool = False


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
        metrics = Metrics(db, settings.qdrant_mode)
        context = AppContext(
            settings=settings,
            db=db,
            index=index,
            providers=registry,
            metrics=metrics,
            agent=Agent(db, index, registry, AnswerCache(db), observer=metrics.observe_trace),
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

    def identify(context: AppContext, request: Request) -> Identity | None:
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if settings.api_key and supplied and hmac.compare_digest(supplied.encode(), settings.api_key.encode()):
            return Identity("API key", "owner")
        cookie = request.cookies.get(SESSION_COOKIE)
        if cookie and (user := context.db.session_user(token_hash(cookie))):
            return Identity(user["email"], user["role"], user["id"])
        if not settings.api_key and context.db.user_count() == 0 and _is_local(request):
            return Identity("local", "owner")  # a fresh install, used from its own machine or network
        return None

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Any:
        path, method = request.url.path, request.method
        context: AppContext | None = getattr(request.app.state, "context", None)
        guarded = (
            (path.startswith("/api/") or path == "/metrics")
            and method != "OPTIONS"
            and path != "/api/system/health"
            and not path.startswith(("/api/share/", "/api/auth/"))  # a share token or sign-in is the credential
        )
        identity = await asyncio.to_thread(identify, context, request) if context else None
        request.state.identity = identity
        if guarded:
            if identity is None:
                return JSONResponse({"detail": "sign in first"}, status_code=401)
            if rank(identity.role) < rank(required_role(method, path)):
                return JSONResponse({"detail": f"a {identity.role} can't do this"}, status_code=403)
        response = await call_next(request)
        if (
            guarded
            and context
            and identity
            and method not in ("GET", "HEAD")
            and not _NOT_AUDITED.fullmatch(f"{method} {path}")
        ):
            route = getattr(request.scope.get("route"), "path", path)
            params = request.scope.get("path_params", {})
            target = params.get("kb_id") or params.get("user_id") or params.get("job_id")
            await asyncio.to_thread(context.db.audit, identity.name, f"{method} {route}", target, response.status_code)
        return response

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
            "share_token": kb.get("share_token"),
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
            "auth": bool(context.settings.api_key) or context.db.user_count() > 0,
            "roles": list(ROLES),
            "data_roots": [str(p) for p in context.settings.data_roots],
        }

    # Accounts. With no accounts and no API key, a local install stays open; the first account turns sign-in on.

    def sign_in(context: AppContext, request: Request, response: Response, user_id: str) -> None:
        token, hashed = new_token()
        context.db.create_session(hashed, user_id, time.time() + SESSION_DAYS * 86400)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=SESSION_DAYS * 86400,
            httponly=True,
            samesite="strict",  # never sent with requests from other sites, so no cross-site forgery
            secure=request.url.scheme == "https",
        )

    def me(context: AppContext, request: Request) -> dict[str, Any]:
        identity: Identity | None = request.state.identity
        user = context.db.get_user(identity.user_id) if identity and identity.user_id else None
        return {
            "accounts": context.db.user_count() > 0,
            "role": identity.role if identity else None,
            "via": None if identity is None else "account" if user else "key" if identity.name == "API key" else "open",
            "key_required": bool(context.settings.api_key),
            "user": {key: user[key] for key in ("id", "email", "name")} if user else None,
        }

    @app.get("/api/auth/me")
    async def auth_me(request: Request, context: Ctx) -> dict[str, Any]:
        return me(context, request)

    @app.post("/api/auth/owner", status_code=201)
    async def create_owner(body: OwnerBody, request: Request, response: Response, context: Ctx) -> dict[str, Any]:
        """The first account. Needs whatever already guards this install: the API key when one is set."""
        if context.db.user_count():
            raise HTTPException(409, "Archie already has an owner; sign in instead")
        identity: Identity | None = request.state.identity
        if identity is None or identity.role != "owner":
            raise HTTPException(401, "enter the admin key first")
        if problem := password_problem(body.password):
            raise HTTPException(422, problem)
        email = body.email.strip().lower()
        user_id = context.db.create_user(email, "owner", body.name.strip(), hash_password(body.password))
        sign_in(context, request, response, user_id)
        context.db.audit(email, "created the owner account", user_id, 201)
        request.state.identity = Identity(email, "owner", user_id)
        return me(context, request)

    @app.post("/api/auth/sign-in")
    async def sign_in_route(body: SignInBody, request: Request, response: Response, context: Ctx) -> dict[str, Any]:
        visitor = request.client.host if request.client else "unknown"
        if not await asyncio.to_thread(context.db.rate_allow, f"sign-in:{visitor}", 10, 60):
            raise HTTPException(429, "Too many attempts. Try again in a minute.", headers={"Retry-After": "60"})
        email = body.email.strip().lower()
        user = context.db.user_login(email)
        stored = user["password_hash"] if user else _DUMMY_HASH
        if not await asyncio.to_thread(check_password, body.password, stored) or user is None:  # scrypt is slow
            context.db.audit(email, "sign-in failed", None, 401)
            raise HTTPException(401, "Wrong email or password.")
        sign_in(context, request, response, user["id"])
        context.db.audit(email, "signed in", user["id"], 200)
        request.state.identity = Identity(email, context.db.get_user(user["id"])["role"], user["id"])  # type: ignore[index]
        return me(context, request)

    @app.post("/api/auth/sign-out", status_code=204)
    async def sign_out(request: Request, response: Response, context: Ctx) -> None:
        if cookie := request.cookies.get(SESSION_COOKIE):
            context.db.end_sessions(token_hash=token_hash(cookie))
        response.delete_cookie(SESSION_COOKIE)

    @app.get("/api/auth/invite/{token}")
    async def invite(token: str, context: Ctx) -> dict[str, Any]:
        user = context.db.user_by_invite(token_hash(token))
        if user is None:
            raise HTTPException(404, "this invitation has expired or was already used")
        return {"email": user["email"], "joined": bool(user["joined"])}

    @app.post("/api/auth/invite/{token}")
    async def accept_invite(
        token: str, body: JoinBody, request: Request, response: Response, context: Ctx
    ) -> dict[str, Any]:
        user = context.db.user_by_invite(token_hash(token))
        if user is None:
            raise HTTPException(404, "this invitation has expired or was already used")
        if problem := password_problem(body.password):
            raise HTTPException(422, problem)
        name = body.name.strip() or user["name"]
        context.db.update_user(
            user["id"], name=name, password_hash=hash_password(body.password), invite_hash=None, invite_expires=None
        )
        context.db.end_sessions(user_id=user["id"])  # a reset link signs out every other device
        sign_in(context, request, response, user["id"])
        context.db.audit(user["email"], "joined" if not user["joined"] else "reset their password", user["id"], 200)
        request.state.identity = Identity(user["email"], user["role"], user["id"])
        return me(context, request)

    @app.post("/api/auth/password", status_code=204)
    async def change_password(body: PasswordBody, request: Request, response: Response, context: Ctx) -> None:
        identity: Identity | None = request.state.identity
        if identity is None or identity.user_id is None:
            raise HTTPException(401, "sign in first")
        user = context.db.user_login(identity.name)
        if user is None or not check_password(body.current, user["password_hash"]):
            raise HTTPException(403, "The current password is wrong.")
        if problem := password_problem(body.new):
            raise HTTPException(422, problem)
        context.db.update_user(identity.user_id, password_hash=hash_password(body.new))
        context.db.end_sessions(user_id=identity.user_id)
        sign_in(context, request, response, identity.user_id)
        context.db.audit(identity.name, "changed their password", identity.user_id, 204)

    # People (owners only, enforced by the middleware)

    def user_or_404(context: AppContext, user_id: str) -> dict[str, Any]:
        user = context.db.get_user(user_id)
        if user is None:
            raise HTTPException(404, "person not found")
        return user

    def keep_an_owner(context: AppContext, user: dict[str, Any]) -> None:
        if user["role"] == "owner" and not user["disabled"] and context.db.owner_count() <= 1:
            raise HTTPException(409, "Archie needs at least one owner; make someone else an owner first")

    def new_invite(context: AppContext, user_id: str) -> str:
        token, hashed = new_token()
        context.db.update_user(user_id, invite_hash=hashed, invite_expires=time.time() + INVITE_DAYS * 86400)
        return token

    @app.get("/api/users")
    async def users(context: Ctx) -> list[dict[str, Any]]:
        return context.db.list_users()

    @app.post("/api/users", status_code=201)
    async def add_user(body: InviteBody, context: Ctx) -> dict[str, Any]:
        email = body.email.strip().lower()
        if context.db.user_by_email(email):
            raise HTTPException(409, "this person already has an account")
        user_id = context.db.create_user(email, body.role)
        return {"user": context.db.get_user(user_id), "invite_token": new_invite(context, user_id)}

    @app.post("/api/users/{user_id}/invite")
    async def reinvite(user_id: str, context: Ctx) -> dict[str, Any]:
        user_or_404(context, user_id)
        return {"invite_token": new_invite(context, user_id)}

    @app.patch("/api/users/{user_id}")
    async def change_user(user_id: str, body: UserChange, context: Ctx) -> dict[str, Any]:
        user = user_or_404(context, user_id)
        if (body.role and body.role != "owner") or body.disabled:
            keep_an_owner(context, user)
        changes = {key: value for key, value in body.model_dump().items() if value is not None}
        if changes:
            context.db.update_user(user_id, **changes)
        if body.disabled:
            context.db.end_sessions(user_id=user_id)
        return user_or_404(context, user_id)

    @app.delete("/api/users/{user_id}", status_code=204)
    async def remove_user(user_id: str, context: Ctx) -> None:
        keep_an_owner(context, user_or_404(context, user_id))
        context.db.delete_user(user_id)

    @app.get("/api/audit")
    async def audit(context: Ctx, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return context.db.audit_log(max(1, min(limit, 500)), max(0, offset))

    @app.get("/api/system/folders")
    async def folders(context: Ctx, path: str | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(list_folders, context.settings, path)

    @app.get("/api/setup/models")
    async def setup_models(context: Ctx, preset: str = "balanced") -> dict[str, Any]:
        """Whether a new chatbot's models are ready, and a built-in search model when the usual one is missing."""
        if preset not in PRESETS:
            raise HTTPException(404, "unknown preset")
        config = preset_config(preset)
        stages: dict[str, Any] = {}
        for name in ("embedding", "generation"):
            stage = getattr(config, name)
            ok = await _model_available(context, stage.provider, stage.model)
            stages[name] = {"provider": stage.provider, "model": stage.model, "ok": ok}
        fallback = None
        if not stages["embedding"]["ok"] and await _model_available(context, *LOCAL_EMBEDDING):
            fallback = {"provider": LOCAL_EMBEDDING[0], "model": LOCAL_EMBEDDING[1]}
        try:
            installed = await context.providers.get("ollama").list_models()
        except Exception:
            installed = []
        return stages | {"fallback_embedding": fallback, "installed": installed}

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
    async def models(context: Ctx, provider: Literal["ollama", "openai", "fastembed"] = "ollama") -> dict[str, Any]:
        return {"provider": provider, "models": await context.providers.get(provider).list_models()}

    # Knowledge bases and configs

    def for_role(request: Request, kb: dict[str, Any]) -> dict[str, Any]:
        """A viewer may ask, not hand the chatbot to others, so the share token is left out."""
        identity: Identity | None = request.state.identity
        return kb if identity and rank(identity.role) >= rank("editor") else kb | {"share_token": None}

    @app.get("/api/kbs")
    async def list_kbs(request: Request, context: Ctx) -> list[dict[str, Any]]:
        return [for_role(request, describe(context, kb)) for kb in context.db.list_kbs()]

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
    async def get_kb(kb_id: str, request: Request, context: Ctx) -> dict[str, Any]:
        kb = kb_or_404(context, kb_id)
        _, config = context.db.get_config(kb_id)
        return for_role(request, describe(context, kb)) | {"config": config.model_dump()}

    @app.delete("/api/kbs/{kb_id}", status_code=204)
    async def delete_kb(kb_id: str, context: Ctx) -> None:
        kb_or_404(context, kb_id)
        if context.db.active_job(kb_id):
            raise HTTPException(409, "cancel the running build first")
        for name in await asyncio.to_thread(context.index.drop_prefix, f"{kb_id}__"):
            context.db.forget_documents(name)
        context.db.delete_kb(kb_id)
        await asyncio.to_thread(shutil.rmtree, context.settings.uploads_dir / kb_id, True)

    @app.post("/api/kbs/{kb_id}/share")
    async def share(kb_id: str, body: ShareBody, context: Ctx) -> dict[str, str | None]:
        kb = kb_or_404(context, kb_id)
        token = kb["share_token"] if body.enabled else None
        if body.enabled and (body.reset or not token):
            token = secrets.token_urlsafe(18)
        context.db.set_share_token(kb_id, token)
        return {"share_token": token}

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
            destination = _free_name(target, name)
            with destination.open("wb") as handle:
                while block := await item.read(1 << 20):
                    size += len(block)
                    if size > limit:
                        handle.close()
                        destination.unlink(missing_ok=True)
                        raise HTTPException(413, f"'{name}' exceeds {context.settings.max_upload_mb} MB")
                    handle.write(block)
            saved.append({"name": destination.name, "bytes": size})
        return {"saved": saved}

    async def suggest(context: AppContext, kb: dict[str, Any]) -> list[str]:
        if not kb["active_collection"]:
            return []
        payloads = await asyncio.to_thread(context.index.sample_payloads, kb["active_collection"], 64)
        return suggest_questions(payloads)

    @app.get("/api/kbs/{kb_id}/suggestions")
    async def suggestions(kb_id: str, context: Ctx) -> list[str]:
        return await suggest(context, kb_or_404(context, kb_id))

    # Documents: how each file was read and chunked, with hand corrections

    def active_collection(context: AppContext, kb_id: str) -> str:
        kb = kb_or_404(context, kb_id)
        if not kb["active_collection"]:
            raise HTTPException(409, "this chatbot has not been built yet")
        return str(kb["active_collection"])

    @app.get("/api/kbs/{kb_id}/documents")
    async def documents(kb_id: str, context: Ctx) -> list[dict[str, Any]]:
        return context.db.document_reports(active_collection(context, kb_id))

    @app.get("/api/kbs/{kb_id}/documents/chunks")
    async def document_chunks(kb_id: str, uri: str, context: Ctx) -> dict[str, Any]:
        collection = active_collection(context, kb_id)
        rows = await asyncio.to_thread(context.index.document_chunks, collection, uri)
        if not rows:
            raise HTTPException(404, "no indexed chunks for that document")
        edits = context.db.chunk_edits(kb_id, uri, str(rows[0].get("content_hash", "")))
        keys = (
            "id",
            "ordinal",
            "text",
            "section",
            "page_start",
            "page_end",
            "content_type",
            "template",
            "token_count",
            "enrichment",
            "excluded",
        )
        chunks = [{key: row.get(key) for key in keys} | {"edited": row.get("ordinal") in edits} for row in rows]
        return {"uri": uri, "title": rows[0].get("title"), "chunks": chunks}

    async def reindex_chunk(
        context: AppContext, kb_id: str, collection: str, payload: dict[str, Any], point: str, text: str
    ) -> None:
        """Re-embed one chunk with new text, the same way the builder would, and replace its point."""
        _, config = context.db.get_config(kb_id)
        searchable = embed_text_for(
            str(payload.get("title", "")), str(payload.get("section", "")), text, config.chunking.section_headers
        )
        enrichment = payload.get("enrichment") or {}
        if extra := Enrichment(**enrichment).search_text():
            searchable = f"{extra}\n\n{searchable}"
        vector = (
            await context.providers.get(config.embedding.provider).embed(
                config.embedding.model, [searchable], "document"
            )
        )[0]
        payload = {**payload, "text": text, "token_count": estimate_tokens(text)}
        await asyncio.to_thread(
            context.index.upsert, collection, [(point, vector, bm25_document_vector(searchable), payload)]
        )

    @app.patch("/api/kbs/{kb_id}/documents/chunks")
    async def edit_chunk(kb_id: str, body: ChunkEditBody, context: Ctx) -> dict[str, Any]:
        collection = active_collection(context, kb_id)
        rows = await asyncio.to_thread(
            context.index.document_chunks, collection, body.uri, (body.ordinal, body.ordinal)
        )
        if not rows:
            raise HTTPException(404, "chunk not found")
        payload = rows[0]
        point = str(payload.pop("id"))
        previous = context.db.chunk_edit(kb_id, body.uri, body.ordinal)
        original = previous["original"] if previous else str(payload.get("text", ""))
        if body.text is not None and body.text != payload.get("text"):
            await reindex_chunk(context, kb_id, collection, payload, point, body.text)
        await asyncio.to_thread(context.index.set_payload, collection, point, {"excluded": body.excluded})
        text = body.text if body.text is not None else (previous["text"] if previous else None)
        context.db.save_chunk_edit(
            kb_id, body.uri, str(payload.get("content_hash", "")), body.ordinal, text, body.excluded, original
        )
        return {"ordinal": body.ordinal, "text": text or payload.get("text"), "excluded": body.excluded}

    @app.delete("/api/kbs/{kb_id}/documents/chunks", status_code=204)
    async def restore_chunk(kb_id: str, uri: str, ordinal: int, context: Ctx) -> None:
        collection = active_collection(context, kb_id)
        previous = context.db.chunk_edit(kb_id, uri, ordinal)
        if previous is None:
            return
        rows = await asyncio.to_thread(context.index.document_chunks, collection, uri, (ordinal, ordinal))
        if rows:
            payload = rows[0]
            point = str(payload.pop("id"))
            if payload.get("text") != previous["original"]:
                await reindex_chunk(context, kb_id, collection, payload, point, previous["original"])
            await asyncio.to_thread(context.index.set_payload, collection, point, {"excluded": False})
        context.db.clear_chunk_edit(kb_id, uri, ordinal)

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

    def answer(context: AppContext, kb_id: str, body: ChatBody) -> StreamingResponse:
        _, config = context.db.get_config(kb_id)
        allowed = {*BUILTIN_FILTER_FIELDS, *config.retrieval.filter_fields}
        if unknown := set(body.filters) - allowed:
            raise HTTPException(422, f"cannot filter on {sorted(unknown)}; allowed: {sorted(allowed)}")
        request = ChatRequest(body.query, body.session_id, dict(body.filters), dict(body.profile))

        async def stream() -> AsyncIterator[str]:
            async for event in context.agent.run(kb_id, request):
                yield _sse(event["type"], json.dumps(event))

        return _sse_response(stream())

    @app.post("/api/kbs/{kb_id}/chat")
    async def chat(kb_id: str, body: ChatBody, context: Ctx) -> StreamingResponse:
        return answer(context, kb_or_404(context, kb_id)["id"], body)

    @app.delete("/api/kbs/{kb_id}/sessions/{session_id}", status_code=204)
    async def clear_session(kb_id: str, session_id: str, context: Ctx) -> None:
        context.db.clear_turns(f"{kb_id}:{session_id}")

    # Shared chatbots: anyone with the link may ask questions, and nothing else

    def shared(context: AppContext, token: str) -> dict[str, Any]:
        kb = context.db.get_kb_by_share(token)
        if kb is None:
            raise HTTPException(404, "this chatbot is not shared, or its link was reset")
        return kb

    @app.get("/api/share/{token}")
    async def shared_info(token: str, context: Ctx) -> dict[str, Any]:
        kb = shared(context, token)
        return {"name": kb["name"], "ready": bool(kb["active_collection"]), "documents": kb["document_count"]}

    @app.get("/api/share/{token}/suggestions")
    async def shared_suggestions(token: str, context: Ctx) -> list[str]:
        return await suggest(context, shared(context, token))

    @app.post("/api/share/{token}/chat")
    async def shared_chat(token: str, body: ChatBody, request: Request, context: Ctx) -> StreamingResponse:
        kb = shared(context, token)
        visitor = request.client.host if request.client else "unknown"
        allow = context.db.rate_allow
        if not await asyncio.to_thread(allow, f"share:{token}:{visitor}", settings.share_rate_per_minute, 60):
            raise HTTPException(429, "Too many questions. Try again in a minute.", headers={"Retry-After": "60"})
        # A ceiling across all visitors, so a script rotating addresses can't run up the model bill.
        if not await asyncio.to_thread(allow, f"share-day:{kb['id']}", settings.share_questions_per_day, 86400):
            detail = "This chatbot has answered all the questions it can today. Try again tomorrow."
            raise HTTPException(429, detail, headers={"Retry-After": "3600"})
        return answer(context, kb["id"], body)

    @app.delete("/api/share/{token}/sessions/{session_id}", status_code=204)
    async def shared_clear_session(token: str, session_id: str, context: Ctx) -> None:
        context.db.clear_turns(f"{shared(context, token)['id']}:{session_id}")

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

    office = sum(
        count for suffix, count in checks.get("source", {}).get("by_type", {}).items() if suffix in LAYOUT_ONLY
    )
    if office and (config.parsing.engine == "fast" or not layout_available()):
        reason = (
            "the reader is set to fast" if config.parsing.engine == "fast" else "the 'layout' extra is not installed"
        )
        warnings.append(
            {
                "stage": "parsing",
                "msg": f"{office} Word, PowerPoint or Excel file(s) will be skipped: {reason}.",
            }
        )
    by_type = checks.get("source", {}).get("by_type", {})
    for kinds, needed, extra in (
        (IMAGE_TYPES, "rapidocr_onnxruntime", "ocr"),
        (MEDIA_TYPES, "faster_whisper", "audio"),
    ):
        count = sum(n for suffix, n in by_type.items() if suffix in kinds)
        if count and importlib.util.find_spec(needed) is None:
            what = "image(s)" if extra == "ocr" else "audio or video file(s)"
            warnings.append(
                {"stage": "parsing", "msg": f"{count} {what} will be skipped: install the '{extra}' extra."}
            )
    if config.enrichment.enabled:
        stats = context.db.collection_stats(kb["active_collection"]) if kb.get("active_collection") else {}
        estimate = f" (about {stats['chunks']} for the last build)" if stats.get("chunks") else ""
        warnings.append(
            {
                "stage": "enrichment",
                "msg": f"Enrichment makes one model call per passage on the first build{estimate}; "
                "unchanged passages are reused after that.",
            }
        )

    stages = ("embedding", "generation", "enrichment") if config.enrichment.enabled else ("embedding", "generation")
    for stage_name in stages:
        stage = getattr(config, stage_name)
        try:
            found = await _model_available(context, stage.provider, stage.model, quiet=False)
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


async def _model_available(context: AppContext, provider: str, model: str, quiet: bool = True) -> bool:
    """Whether ``provider`` serves ``model`` (Ollama tags count: 'qwen3.5' matches 'qwen3.5:9b')."""
    try:
        available = await context.providers.get(provider).list_models()
    except Exception:
        if quiet:
            return False
        raise
    return any(m == model or m.startswith(model + ":") for m in available)


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


def _security_headers(path: str) -> dict[str, str]:
    """The headers nginx sends in the split setup; shared chatbots may be framed by any site (the widget)."""
    shared = path.startswith("/share/")
    ancestors = "*" if shared else "'none'"
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        f"connect-src 'self'; frame-ancestors {ancestors}",
    }
    return headers if shared else headers | {"X-Frame-Options": "DENY"}


def _free_name(folder: Path, name: str) -> Path:
    """``name`` in ``folder``, or 'name 2.pdf', 'name 3.pdf'... when taken (as the Finder names copies)."""
    candidate, stem, suffix, number = folder / name, Path(name).stem, Path(name).suffix, 2
    while candidate.exists():
        candidate, number = folder / f"{stem} {number}{suffix}", number + 1
    return candidate


def _is_local(request: Request) -> bool:
    """The client is this machine or a private network (after trusted proxies are resolved)."""
    host = request.client.host if request.client else ""
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return True  # not an IP address: a Unix socket or the test client, never a remote visitor


def _mount_ui(app: FastAPI, dist: Path) -> None:
    @app.middleware("http")
    async def secure_pages(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers.update(_security_headers(request.url.path))
        return response

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
