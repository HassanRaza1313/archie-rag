"""Build the benchmark corpus under several configs and compare retrieval on the golden set.

Usage: python bench/run.py [config ...]     (default: every config below)
Runs fully locally: FastEmbed embeddings and reranker, embedded Qdrant, no model server. Results are printed
as a table and written to bench/results.json. Generate the corpus first with bench/make_corpus.py.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from ragkit.config import KnowledgeBaseConfig, deep_merge
from ragkit.evaluation import GoldenItem, evaluate
from ragkit.index import VectorIndex
from ragkit.jobs import BUILD_STAGES, Builder
from ragkit.providers import ProviderRegistry
from ragkit.settings import Settings
from ragkit.storage import Database

BENCH = Path(__file__).resolve().parent
CORPUS = BENCH.parent / "data" / "sources" / "bench"
STATE = BENCH / ".state"

COMMON: dict[str, Any] = {
    "source": {"path": str(CORPUS)},
    "embedding": {"provider": "fastembed", "model": "BAAI/bge-small-en-v1.5"},
}
# Each config is an overlay on the defaults; later entries add one improvement at a time.
CONFIGS: dict[str, dict[str, Any]] = {
    "baseline": {"parsing": {"engine": "fast"}, "chunking": {"strategy": "structure"}},
    "layout": {"parsing": {"engine": "layout"}, "chunking": {"strategy": "structure"}},
    "auto": {"parsing": {"engine": "auto"}, "chunking": {"strategy": "structure"}},
    "auto+templates": {"parsing": {"engine": "auto"}, "chunking": {"strategy": "auto"}},
}
METRICS = [
    "passage_hit_at_1",
    "passage_hit_at_5",
    "passage_mrr",
    "hit_at_1",
    "mrr",
    "correct_abstain_rate",
]


async def run_one(
    name: str, overlay: dict[str, Any], items: list[GoldenItem]
) -> dict[str, Any]:
    state = STATE / name
    shutil.rmtree(state, ignore_errors=True)
    settings = Settings(
        state_dir=state,
        data_roots=[CORPUS],
        model_cache_dir=BENCH.parent / ".ragkit" / "models",
        log_level="WARNING",
    )
    config = KnowledgeBaseConfig.model_validate(
        deep_merge(deep_merge(KnowledgeBaseConfig().model_dump(), COMMON), overlay)
    )
    db = Database(settings.db_path)
    kb_id = db.create_kb(f"bench-{name}", config)
    version, _ = db.get_config(kb_id)
    db.create_job(kb_id, version, BUILD_STAGES)
    job = db.claim_job()
    assert job is not None
    providers = ProviderRegistry(settings)
    index = VectorIndex(settings)
    try:
        started = time.perf_counter()
        status = await Builder(db, index, providers, settings).run(job)
        build_s = time.perf_counter() - started
        result = db.get_job(job["id"]) or {}
        if status not in ("completed", "completed_with_errors"):
            return {"status": status, "error": result.get("error")}
        report = await evaluate(db, index, providers, kb_id, items)
    finally:
        await providers.close()
        index.close()
    misses = [
        row["question"]
        for row in report["results"]
        if row["answerable"] and not row["passage_rank"]
    ]
    return {
        "status": status,
        "build_s": round(build_s, 1),
        "chunks": (result.get("summary") or {}).get("total_chunks"),
        "failures": len(result.get("failures") or []),
        **report["metrics"],
        "passage_misses": misses,
    }


def table(results: dict[str, dict[str, Any]]) -> str:
    head = ["config", "build s", "chunks", *METRICS]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for name, row in results.items():
        cells = [
            name,
            row.get("build_s", "-"),
            row.get("chunks", "-"),
            *(row.get(m, row.get("error", "-")) for m in METRICS),
        ]
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    return "\n".join(lines)


def main(names: list[str]) -> None:
    if not CORPUS.is_dir():
        sys.exit(f"no corpus at {CORPUS}; run python bench/make_corpus.py first")
    items = [
        GoldenItem.model_validate_json(line)
        for line in (BENCH / "golden.jsonl").read_text().splitlines()
        if line
    ]
    results_file = BENCH / "results.json"
    results = json.loads(results_file.read_text()) if results_file.exists() else {}
    for name in names or list(CONFIGS):
        print(f"building {name}…", flush=True)
        results[name] = asyncio.run(run_one(name, CONFIGS[name], items))
    results_file.write_text(json.dumps(results, indent=2) + "\n")
    print(table({name: results[name] for name in CONFIGS if name in results}))


if __name__ == "__main__":
    main(sys.argv[1:])
