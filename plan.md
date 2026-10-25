# Plan — Configurable Agentic RAG Framework

> Status: **M0–M8 built; M9 partly done** (see §9). Companion docs: [architecture.md](architecture.md)
> (as built, including every deviation from this plan) and [setup.md](setup.md) (how to run it).
> Working name for the package is `ragkit`; rename freely.

## 1. Goal

An open-source, clone-and-run framework for building **agentic RAG chatbots over your own documents**.
A person setting up a chatbot:

1. points it at a data source (a local folder today; other sources later),
2. walks through one settings page per pipeline stage — only the settings that matter, each explained in one line,
3. clicks **Build**, which runs a background job that provisions the vector index and ingests the corpus,
4. chats with the result, and can see a trace of exactly what every request retrieved, filtered, and decided.

It must run equally well as plain local code (hot reload, debugger) or as Docker containers (one command,
any machine), and every infrastructure piece (UI, API, worker, vector DB, model inference) can run separately.

## 2. Locked decisions

| Area | Decision | Why |
|---|---|---|
| Agent | **Bounded agent**: explicit state machine, capped iterations | Predictable latency on local models; every decision is traceable |
| Vector DB | **Qdrant server (Docker) by default**, embedded mode as no-Docker fallback | Server gives real HNSW, payload indexes, native BM25 sparse vectors, server-side RRF/DBSF |
| Frontend | **React + TypeScript + Vite SPA**, Tailwind, shadcn/ui, TanStack Query, SSE | Industry-standard, minimal, fast; streams chat and job progress |
| Backend | FastAPI (API) + separate **worker** process (jobs) | Long jobs never block or time out the API |
| App state | SQLite (configs, jobs, traces, cache) behind a repository layer | Zero extra services; swappable for Postgres |
| Models | Pluggable providers: **Ollama** (default) and **any OpenAI-compatible endpoint** (LM Studio, vLLM, llama.cpp, OpenAI) | People cloning the repo have different hardware |
| Audience | Local single user by default; optional API key; public GitHub repo | Clone-and-run for anyone |
| Multi-chatbot | Several **knowledge bases** per install, each with **versioned configs** | Compare configs; rebuild without downtime |
| Parsing | **pdfium** text + pdfplumber tables + optional OCR (revised from Docling during build) | Measured 7× faster than pdfminer, permissive licence, no PyTorch; PyMuPDF is AGPL |
| Reranking | **FastEmbed ONNX** cross-encoder; MiniLM-L-6 default, bge-reranker-base in High accuracy | No PyTorch in images; MiniLM measured 7× faster than bge on CPU with near-identical discrimination |
| Telemetry | OpenTelemetry-shaped spans → local SQLite store (UI); OTLP exporter still open | No extra dependency until OTLP is needed |
| Quality loop | **Evaluation is first-class** (golden set, hit@k, MRR, abstain rate per config version) | Tuning knobs without measurement is guessing |
| Deployment | Same code locally or in containers; `rag` CLI (bash + PowerShell twins over one Python CLI); split images, all-in-one image, GHCR releases | One command / double-click for everyone |
| License | MIT (switch to Apache-2.0 if a patent grant is wanted) | Permissive, common for frameworks |

Where these choices deviate from the original spec, architecture.md §13 lists each one with its reason.

## 3. Configuration philosophy

Every value lives in **one typed config schema** (satisfies "nothing hardcoded"), but it is surfaced in tiers:

- **Essentials** — shown on each stage page (2–4 fields). What most people change.
- **Advanced** — collapsed on the same page. Rarely needed, still explained.
- **Expert** — YAML only (import/export), not in the UI.
- **Presets** — `Balanced` (default), `High accuracy`, `Low latency`, plus hardware presets `Laptop CPU` / `GPU workstation`.

Settings are split by lifecycle:

- **Index-time** (source, parsing, chunking, embedding, index) — baked into vectors; changing them marks the
  chatbot **stale** and requires a rebuild. Badged "requires rebuild" in the UI.
- **Query-time** (retrieval, fusion, reranking, context, agent, generation, cache, memory, privacy-at-query) —
  take effect immediately as a new config version, no rebuild.

The schema carries UI metadata (label, help, tier, stage, requires-rebuild, min/max), and the frontend renders
stage pages **from the schema**, so config, UI and docs cannot drift. Full field list: architecture.md §4.

Infrastructure settings (service URLs, ports, API key, Qdrant mode, data roots) are **environment variables**
(`.env`), not chatbot config — the same image runs anywhere.

## 4. Pipeline stages (what's configurable, at a glance)

| Stage | Essentials | Advanced |
|---|---|---|
| Source | type (local folder / uploads), path, file types | include/exclude globs, recursive |
| Parsing | OCR (off / auto / force), tables (markdown / off) | strip headers & footers |
| Chunking | strategy (structure-aware ★ / fixed), size (tokens), overlap % | section-path headers on chunks |
| Embedding | provider + model (dims auto-detected) | batch size |
| Index | — (defaults) | HNSW m / ef_construct, int8 quantization |
| Retrieval | mode (hybrid ★ / dense / keyword), candidates per retriever | search ef, filterable metadata fields |
| Fusion | RRF ★ / DBSF | RRF k |
| Reranking | on/off, model, final chunk count | min relevance score |
| Context | token budget, dedup on/off | near-duplicate threshold, compression (off / extractive) |
| Agent | max retries, confidence threshold, answer verification on/off | allowed strategies (rewrite / decompose / broaden), on-fail (flag / abstain) |
| Generation | model, temperature, system prompt | prompt template, max tokens, context window, thinking on/off |
| Cache & memory | cache on/off, similarity threshold, memory on/off | cache / history capacity, turns kept |
| Privacy | entity types to redact | apply at ingest / prompt / logs |
| Telemetry | level (minimal / standard ★ / verbose) | retention |

## 5. What changes vs. the current codebase

The current Codex code is replaced, not patched. Kept as ideas: the service split, Ollama + Qdrant + BGE choice.
Fixed by design (from the review):

- Ingestion never running (queued flag), invalid Qdrant point IDs, removed `client.search` API → new job model,
  deterministic UUIDv5 point IDs, Qdrant Query API.
- Reranker fallback that always abstains, mixed-scale confidence → calibrated confidence (architecture.md §6.4).
- Fake retries (same embedding + noise string) → real reformulation with a new embedding.
- Blocking CPU work on the event loop → worker process + threadpool; batched, pooled model calls.
- In-memory BM25 + brute-force "HNSW" → Qdrant native sparse vectors + server-side fusion.
- Page-bound chunking, duplicated tables, weak titles → structure-aware markdown chunking across pages.
- Global cache leaking across users, stale after re-ingest → cache scoped per chatbot + index version.
- Regex-only PII with false positives → validated detectors (Luhn, IBAN checksum, phone parsing), optional NER.
- Invisible log fields, no request summary → one-line request logs + a queryable trace store.
- Streamlit → React SPA. Missing docs → plan/architecture/setup/extending.

Repo hygiene: **`dataset/` (copyrighted books) is git-ignored and never pushed**; a small public-domain sample
corpus ships in `data/sources/sample/` so the first run works out of the box.

## 6. Milestones (build order)

Each milestone ends green: lint, types, tests, and a runnable demo of what it adds.

| # | Milestone | Delivers | Done when |
|---|---|---|---|
| M0 | Repo foundation | New layout, `uv` + lockfile, ruff/mypy/pytest, Vite scaffold, CI, `.env.example`, licence, sample corpus, old code removed | `rag doctor` and CI pass on an empty app |
| M1 | Core | Config schema + presets + registry, providers (Ollama, OpenAI-compatible) with batched embeddings, SQLite storage, JSON-schema endpoint | Schema renders to JSON Schema; provider contract tests pass |
| M2 | Ingestion | Sources (local folder, uploads), parsers (PDF + OCR, MD/TXT), chunkers, ingest-time privacy, `VectorStore` (server + embedded), incremental sync | Sample corpus indexes in both Qdrant modes; re-run skips unchanged files |
| M3 | Jobs | Worker, job/stage/item tables, cancel/resume, build pipeline with stage telemetry, smoke test, active-collection swap | Build runs from CLI; progress visible via API; kill-and-resume works |
| M4 | Query pipeline + eval | Hybrid retrieval, fusion, reranker, dedup, extractive compression, token budget; golden-set eval harness (CLI) | Hybrid + rerank beats dense-only on hit@5 / MRR for the sample set; latency budgets measured |
| M5 | Agent | State machine, follow-up condensing, cache, memory, verification, streaming, abstention | Scenario tests: follow-up, zero results, low confidence, cache hit, verify-fail |
| M6 | API | REST + SSE routes, optional API key, limits, CORS, OpenAPI → generated TS client | Contract tests; client regenerates cleanly |
| M7 | Frontend | Design system + shell, Chatbots home, Setup (schema-driven), Build monitor, Chat + trace drawer, Telemetry, Evaluate | End-to-end: create → build → chat → inspect trace → compare two versions, in the browser |
| M8 | Containers & scripts | Dockerfiles (api, ui, aio), compose (profiles + GPU override + release), `rag` CLI, double-click launchers, GHCR multi-arch release workflow | Fresh machine: clone → `rag up` → working app; same for `rag dev` and `rag run` |
| M9 | Docs & hardening | README quickstart, extending guide, security pass, load test of the query path | A newcomer follows README alone; p95 budgets met on the reference machine |

Stretch (after M9): DOCX/HTML parsers, more data sources (S3, Confluence, SharePoint), Postgres backend.

## 7. Testing strategy

- **Unit**: chunkers, fusion, confidence, privacy detectors, cache promotion, agent transitions (pure functions, no I/O).
- **Contract**: providers and vector stores against fakes and against real Qdrant embedded mode in CI.
- **Scenario**: agent end-to-end with a fake LLM — follow-up, zero results, low confidence → retry → abstain, verify-fail.
- **API**: FastAPI TestClient incl. SSE.
- **Frontend**: Vitest for logic and components; the full browser flow was verified manually (a Playwright
  suite is still open, §9).
- **Eval**: labelled Q/A set over the sample corpus (`data/eval/sample-golden.jsonl`); reported per config version.

## 8. Open items

- Package / product name (placeholder `ragkit`).
- Licence: shipped as MIT; switch to Apache-2.0 if a patent grant is wanted.
- Reference machine for latency budgets (your CPU/GPU, RAM).

## 9. Status

| Milestone | Status | Notes |
|---|---|---|
| M0 Repo foundation | Done | Legacy code removed; uv lockfile; ruff, mypy, Biome; CI workflow; MIT licence; sample corpus |
| M1 Core | Done | Config schema + presets, providers (Ollama, OpenAI-compatible), SQLite storage |
| M2 Ingestion | Done | pdfium parser (measured 7× faster than pdfminer), structure-aware chunking, incremental sync |
| M3 Jobs | Done | Worker, honest statuses, cancel/resume, process pool with in-process fallback |
| M4 Query + eval | Done | Hybrid retrieval, fusion, reranker (default chosen from a benchmark), eval harness |
| M5 Agent | Done | Condense, route, cache, decompose, retry, verify, abstain, streaming |
| M6 API | Done | REST + SSE, API key, limits, filter allowlist, upload checks |
| M7 Frontend | Done | Five screens, responsive, light/dark; verified in a browser end to end |
| M8 Containers & scripts | Built, not run here | No Docker on the build machine; CI builds and smoke-tests the images |
| M9 Docs & hardening | Partly | Docs done; still open: Playwright e2e, a load test, a real-Ollama end-to-end run, OTLP exporter |

Backend: 156 tests, 95 % line coverage. Frontend: 17 tests. Lint, format and type checks are clean on both.
