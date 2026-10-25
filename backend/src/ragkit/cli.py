"""Command line: ``ragkit serve | worker | kb | eval | doctor``.

The ``scripts/rag.*`` wrappers and the container images call this, so behaviour is identical everywhere.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import shutil
import socket
import sys
from pathlib import Path
from typing import Any

import yaml

from ragkit.config import PRESETS, KnowledgeBaseConfig, deep_merge, preset_config
from ragkit.settings import Settings, load_dotenv
from ragkit.telemetry import configure_logging


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    settings = Settings.from_env()
    configure_logging(settings.log_level, settings.log_format)
    parser = argparse.ArgumentParser(prog="ragkit", description="Configurable agentic RAG framework.")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the API (and UI, if built)")
    serve.add_argument("--with-worker", action="store_true", help="run build jobs in this process")
    serve.add_argument("--reload", action="store_true", help="auto-reload on code changes (development)")

    commands.add_parser("worker", help="run build jobs (server-mode Qdrant only)")

    kb = commands.add_parser("kb", help="manage knowledge bases headlessly")
    kb_commands = kb.add_subparsers(dest="kb_command", required=True)
    kb_list = kb_commands.add_parser("list")
    kb_list.set_defaults(kb_command="list")
    build = kb_commands.add_parser("build", help="create or update a knowledge base from YAML and build it")
    build.add_argument("name")
    build.add_argument("--config", type=Path, help="YAML config (partial; merged over the preset)")
    build.add_argument("--preset", default="balanced", choices=sorted(PRESETS))
    export = kb_commands.add_parser("export", help="print a knowledge base's active config as YAML")
    export.add_argument("name")

    evaluate_cmd = commands.add_parser("eval", help="evaluate retrieval on a golden set (JSONL)")
    evaluate_cmd.add_argument("name")
    evaluate_cmd.add_argument("golden", type=Path)
    evaluate_cmd.add_argument("--version", type=int)

    commands.add_parser("doctor", help="check that every dependency is reachable")

    args = parser.parse_args(argv)
    if args.command == "serve":
        return _serve(settings, args.with_worker, args.reload)
    if args.command == "worker":
        return _worker(settings)
    if args.command == "kb":
        return asyncio.run(_kb(settings, args))
    if args.command == "eval":
        return asyncio.run(_eval(settings, args.name, args.golden, args.version))
    return asyncio.run(_doctor(settings))


def _serve(settings: Settings, with_worker: bool, reload: bool) -> int:
    import uvicorn

    if not settings.is_loopback and not settings.api_key and not settings.public_bind_ok:
        print(
            "refusing to listen on a non-loopback address without RAG_API_KEY (containers: RAG_PUBLIC_BIND_OK)",
            file=sys.stderr,
        )
        return 2
    if settings.qdrant_mode == "embedded" and not with_worker:
        print("embedded Qdrant allows one process: running the worker in-process (--with-worker)")
        with_worker = True
    if reload:
        if with_worker:
            import os

            os.environ["RAGKIT_IN_PROCESS_WORKER"] = "1"
        uvicorn.run(
            "ragkit.cli:reload_app",
            factory=True,
            host=settings.bind_host,
            port=settings.port,
            reload=True,
            access_log=False,
            reload_dirs=[str(Path(__file__).parent)],
        )
        return 0
    from ragkit.api import create_app

    uvicorn.run(
        create_app(settings, in_process_worker=with_worker),
        host=settings.bind_host,
        port=settings.port,
        log_config=None,
        access_log=False,
    )
    return 0


def reload_app() -> Any:
    import os

    from ragkit.api import create_app

    load_dotenv()
    settings = Settings.from_env()
    configure_logging(settings.log_level, settings.log_format)
    return create_app(settings, in_process_worker=os.environ.get("RAGKIT_IN_PROCESS_WORKER") == "1")


def _worker(settings: Settings) -> int:
    if settings.qdrant_mode == "embedded":
        print("embedded Qdrant allows one process; use 'ragkit serve --with-worker'", file=sys.stderr)
        return 2
    from ragkit.index import VectorIndex
    from ragkit.jobs import Builder, run_worker
    from ragkit.providers import ProviderRegistry
    from ragkit.storage import Database

    async def run() -> None:
        providers = ProviderRegistry(settings)
        try:
            await run_worker(Builder(Database(settings.db_path), VectorIndex(settings), providers, settings))
        finally:
            await providers.close()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())
    return 0


async def _kb(settings: Settings, args: argparse.Namespace) -> int:
    from ragkit.index import VectorIndex
    from ragkit.jobs import BUILD_STAGES, Builder
    from ragkit.providers import ProviderRegistry
    from ragkit.storage import Database

    db = Database(settings.db_path)
    if args.kb_command == "list":
        for row in db.list_kbs():
            print(
                f"{row['id']}  {row['name']:<30} v{row['active_version']}  docs={row['document_count']} "
                f"chunks={row['chunk_count']}"
            )
        return 0
    existing = db.get_kb_by_name(args.name)
    if args.kb_command == "export":
        if not existing:
            print(f"no knowledge base named '{args.name}'", file=sys.stderr)
            return 1
        print(yaml.safe_dump(db.get_config(existing["id"])[1].model_dump(), sort_keys=False))
        return 0
    overrides = yaml.safe_load(args.config.read_text(encoding="utf-8")) if args.config else {}
    config = KnowledgeBaseConfig.model_validate(deep_merge(preset_config(args.preset).model_dump(), overrides or {}))
    if existing:
        kb_id = existing["id"]
        db.add_config_version(kb_id, config)
    else:
        kb_id = db.create_kb(args.name, config)
    version, _ = db.get_config(kb_id)
    job_id = db.create_job(kb_id, version, BUILD_STAGES)
    job = db.claim_job()
    assert job is not None and job["id"] == job_id
    providers = ProviderRegistry(settings)
    try:
        status = await Builder(db, VectorIndex(settings), providers, settings).run(job)
    finally:
        await providers.close()
    result = db.get_job(job_id) or {}
    print(
        json.dumps(
            {
                "kb_id": kb_id,
                "status": status,
                "error": result.get("error"),
                "summary": result.get("summary"),
                "failures": result.get("failures"),
            },
            indent=2,
        )
    )
    return 0 if status in ("completed", "completed_with_errors") else 1


async def _eval(settings: Settings, name: str, golden: Path, version: int | None) -> int:
    from ragkit.evaluation import GoldenItem, evaluate
    from ragkit.index import VectorIndex
    from ragkit.providers import ProviderRegistry
    from ragkit.storage import Database

    db = Database(settings.db_path)
    kb = db.get_kb_by_name(name)
    if kb is None:
        print(f"no knowledge base named '{name}'", file=sys.stderr)
        return 1
    items = [
        GoldenItem.model_validate_json(line) for line in golden.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    providers = ProviderRegistry(settings)
    try:
        report = await evaluate(db, VectorIndex(settings), providers, kb["id"], items, version)
    finally:
        await providers.close()
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
    return 0


async def _doctor(settings: Settings) -> int:
    from ragkit.index import VectorIndex
    from ragkit.providers import ProviderRegistry

    checks: list[tuple[str, bool, str]] = []
    checks.append(("python >= 3.10", sys.version_info >= (3, 10), sys.version.split()[0]))
    for root in settings.data_roots:
        checks.append((f"data root {root}", Path(root).is_dir(), "create it or set RAG_DATA_ROOTS"))
    try:
        index_ok = VectorIndex(settings).ping()
    except Exception as exc:
        index_ok = False
        detail = str(exc)[:120]
    else:
        detail = settings.qdrant_url if settings.qdrant_mode == "server" else str(settings.embedded_qdrant_path)
    checks.append((f"qdrant ({settings.qdrant_mode})", index_ok, detail))
    providers = ProviderRegistry(settings)
    try:
        ollama = providers.get("ollama")
        reachable = await ollama.ping()
        models = await ollama.list_models() if reachable else []
        checks.append(("ollama reachable", reachable, settings.ollama_url))
        for model in (KnowledgeBaseConfig().embedding.model, KnowledgeBaseConfig().generation.model):
            present = any(m == model or m.startswith(model + ":") for m in models)
            checks.append((f"model {model}", present, "ollama pull " + model))
    finally:
        await providers.close()
    try:
        import fastembed  # noqa: F401

        checks.append(("reranker (fastembed)", True, "installed"))
    except ImportError:
        checks.append(("reranker (fastembed)", settings.rerank_provider == "none", "pip install 'ragkit[rerank]'"))
    checks.append(("node (UI development only)", shutil.which("node") is not None, "https://nodejs.org"))
    with socket.socket() as probe:
        free = probe.connect_ex((settings.bind_host, settings.port)) != 0
    checks.append((f"port {settings.port} free", free, "set RAG_PORT"))
    for name, ok, hint in checks:
        print(f"  {'ok' if ok else 'FAIL':<4}  {name:<34} {'' if ok else hint}")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
