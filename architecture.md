# Architecture

> As built. Companion to [plan.md](plan.md) (scope, decisions, status) and [setup.md](setup.md) (running it).
> Benchmark for every choice: what a top-tier production RAG team would ship. §12 lists every deviation from the
> original spec and from the first design draft, with the evidence behind it.

## 1. Principles

1. **Retrieval quality first, agent second.** Parsing, chunking, hybrid retrieval and reranking decide most answer
   quality. The agent loop is a bounded safety net.
2. **Measure, don't guess.** Every config version can be scored on a golden set; every request leaves a trace;
   defaults were chosen from measurements on real documents (§11).
3. **Few knobs, good defaults.** Settings are tiered (Essentials / Advanced) per stage, with three presets.
4. **Long work never runs in a request.** The API enqueues builds; a worker runs them; the UI streams progress.
5. **Same code everywhere.** Local process, split containers or one all-in-one container: only environment changes.
6. **Flat, boring, auditable.** One module per concern, plain functions, stdlib where it is enough, registries
   (plain dicts) at the extension seams.

## 2. Topology

```
 browser ─▶ UI (React SPA) ──REST + SSE──▶ API (FastAPI) ◀── SQLite (WAL) ──▶ Worker (builds)
                                              │   query                         │   upsert
                                              ▼                                 ▼
                                   Qdrant: server (Docker) | embedded (local path)
                                   Models: Ollama | any OpenAI-compatible server; reranker: FastEmbed ONNX
```

| Process | Responsibility | Notes |
|---|---|---|
| UI | Static SPA; nginx in the split setup, served by the API in local and all-in-one runs | ~130 KB gzipped |
| API | Config CRUD, chat (agent), traces, job control, uploads, evaluation | Async I/O; CPU work in threads |
| Worker | Build jobs: claims queued jobs atomically, heartbeats, resumable | Separate process in server mode, in-process in embedded mode (embedded Qdrant allows one process per path) |
| Qdrant | Dense + BM25 sparse vectors, payload indexes | Server: real HNSW. Embedded: same API, brute force |
| Models | Embeddings + chat over HTTP; reranker in-process | Every URL comes from the environment |

## 3. Repository layout

```
backend/src/ragkit/
  settings.py     infrastructure settings from RAG_* env vars and .env
  config.py       chatbot config: one model per stage, UI metadata, presets, fingerprints
  storage.py      SQLite: knowledge bases, config versions, jobs, indexed docs, traces, answer cache
  providers.py    Ollama + OpenAI-compatible (embed, streaming chat, model list), FastEmbed reranker
  sources.py      data sources (local folder, uploads) with the data-root allowlist
  parsing.py      PDF (pdfium text + pdfplumber tables + optional OCR), Markdown, text
  chunking.py     structure-aware and fixed chunking
  textutil.py     token estimate, sentences, BM25 terms, shingles
  index.py        Qdrant collections, upserts, batched dense + keyword search
  retrieval.py    fusion, rerank, relevance floor, dedup, compression, token budget
  privacy.py      validated PII detectors and consistent redaction
  cache.py        answer cache: exact + guarded paraphrase match, frequency promotion
  memory.py       bounded session memory
  agent.py        the bounded agent (state machine) that answers a chat request
  telemetry.py    request traces, one-line request logs, JSON logging
  jobs.py         build pipeline and worker loop
  evaluation.py   golden-set retrieval evaluation
  api.py          HTTP API + SSE + static UI
  cli.py          ragkit serve | worker | kb | eval | doctor
backend/tests/    156 tests: unit, integration (embedded Qdrant), agent scenarios, API, CLI
frontend/src/     lib/ (typed API client, schema → form model), components/, features/<screen>/
deploy/           Dockerfile (targets api, ui, aio), nginx template, compose (+ gpu, release overrides)
scripts/          rag.sh / rag.ps1 (same commands), double-click launchers
data/             sources/sample (public sample corpus), eval/sample-golden.jsonl
```

## 4. Configuration

### 4.1 Two layers
- **Infrastructure** (`settings.py`, `.env`): where things run (URLs, paths, ports, API key, Qdrant mode).
- **Chatbot config** (`config.py`): what a chatbot does. Versioned per knowledge base in SQLite; exportable as
  YAML (`ragkit kb export`); importable (`ragkit kb build NAME --config file.yaml`).

### 4.2 Schema drives the UI
Each stage is a Pydantic model; each field carries `title` (label), `description` (one-line help) and
`tier` (essential | advanced); each stage carries `summary` and `rebuild`. `GET /api/schema` returns the JSON
Schema and the frontend renders the setup pages from it. Adding a field in Python adds it to the UI.

### 4.3 Stages and defaults (★ default)

| Stage (⟳ = requires rebuild) | Essentials | Advanced |
|---|---|---|
| Source ⟳ | type (local folder ★ / uploads), folder, file types (.pdf .md .txt) | include/exclude globs, recursive |
| Parsing ⟳ | OCR (auto ★ / off / force), tables (markdown ★ / off) | strip running headers/footers |
| Chunking ⟳ | strategy (structure ★ / fixed), size 512 tokens, overlap 12 % | section path in embedding text |
| Embedding ⟳ | provider (ollama ★ / openai), model (nomic-embed-text) | batch size 32 |
| Index ⟳ | – | HNSW m 16, ef_construct 128, int8 quantization |
| Retrieval | mode (hybrid ★ / dense / keyword), candidates 20 | search ef 64, filterable fields |
| Fusion | rrf ★ / dbsf | RRF k 60 |
| Reranking | enabled, model (MiniLM-L-6 ★), final chunks 6 | minimum relevance 0.2 |
| Context | token budget 3000, dedup on | near-duplicate threshold 0.85, compression (off ★ / extractive) |
| Agent | max retries 1, confidence threshold 0.35, answer check (citations ★ / llm / off) | strategies (rewrite, broaden, decompose), on fail (flag ★ / abstain), abstain message |
| Generation | provider, model (qwen3.5:9b), temperature 0.1, system prompt | prompt template, max tokens, context window, thinking |
| Cache & memory | answer cache, conversation memory | paraphrase matching + threshold 0.95, cache 25 / history 100, turns 6 |
| Privacy | detectors (email, phone, card, IBAN, IP, national ID) | redact sources to the model (auto ★ / always / never), redact at ingest |
| Telemetry | level (minimal / standard ★ / verbose) | retention 14 days |

Presets: `balanced` (defaults), `high_accuracy` (bge-reranker-base, 40 candidates, 2 retries, LLM answer check,
decomposition, no paraphrase cache), `low_latency` (12 candidates, top 4, no retries, shorter answers).

### 4.4 Versions and staleness
Saving creates an immutable version. `index_fingerprint()` hashes the ⟳ stages; when the active version's
fingerprint differs from the built index, the chatbot is **stale** and the UI says a rebuild is needed. Query-time
edits apply on the next request. `query_fingerprint()` scopes the answer cache.

## 5. Build pipeline (`jobs.py`)

`validate → provision → discover → parse → chunk → embed → index → reconcile → smoke_test → activate`

- **validate**: source has supported files; the embedding model answers a probe (its dimension sizes the index).
- **provision**: collection `kb_<id>__<index-fingerprint>` with a named dense vector (cosine, HNSW, optional int8)
  and a BM25 sparse vector (`modifier=idf`); payload indexes in server mode.
- **discover**: SHA-256 per file; files whose hash is already indexed in this collection are skipped. This makes
  rebuilds incremental and interrupted builds resumable.
- **parse**: PDFs in a process pool when there are several (CPU-bound); other files in a thread. A broken pool
  falls back to in-process parsing instead of failing the build.
- **chunk / embed / index**: batched embeddings; deterministic point ids `uuid5(kb, content_hash, ordinal)`;
  a changed document's old points are deleted before its new ones are written.
- **reconcile**: deletes documents removed from the source.
- **smoke_test**: three searches built from indexed chunks must return results.
- **activate**: atomically points the chatbot at the new collection and drops the previous one. Chat keeps using
  the old index until this moment, so rebuilds have no downtime.

Honest statuses: `completed`, `completed_with_errors` (some documents failed; listed with stage and reason),
`failed` (nothing indexed, smoke test failed, or a fatal error), `cancelled`. Per-stage counts (in, out, skipped,
failed), timings and throughput are recorded and streamed to the UI.

### 5.1 Parsing (`parsing.py`)
- **Text**: pdfium (pypdfium2, Apache-2.0/BSD) per character: size, weight, position. About 50 ms per page on the
  test machine, versus about 335 ms with pdfminer layout analysis.
- **Headings**: lines ≥ 1.18 × body size, or bold, standalone, title-case lines at body size (books often mark
  headings only with bold). "Contents" never becomes a parent section.
- **Paragraphs**: vertical gap above the lower-quartile line gap, or first-line indent after a sentence end;
  paragraphs continue across page breaks; pdfium's hyphenation marker is removed.
- **Running headers/footers**: repeated margin lines, or small repeated lines at the same height in the outer
  15 % bands; letter-spaced small caps are collapsed; roman page numbers ignored.
- **Duplicates**: characters drawn twice at the same spot (fake bold) are kept once.
- **Tables**: pdfplumber's table finder runs only on pages with ≥ 4 drawn paths; tables become markdown and their
  text is removed from the paragraph flow.
- **OCR**: pages without a text layer are OCR'd with RapidOCR when the `ocr` extra is installed; otherwise the
  build reports a warning.

### 5.2 Chunking (`chunking.py`)
`structure`: packs whole paragraphs within a section up to `chunk_size` tokens; oversized paragraphs split on
sentences, then words; overlap = whole trailing sentences (≈ overlap %) inside the section; chunks cross pages
(page span kept) but not sections, except tiny sections, which merge forward. Tables are separate chunks; large tables
split by rows with the header repeated. The embedded text is prefixed with `document title › section path`.
`fixed`: equal windows with overlap, snapped to word boundaries.

## 6. Query path: the bounded agent (`agent.py`)

```
prepare ─(follow-up?)─▶ condense ─▶ route ─smalltalk─▶ reply
                                      │retrieve
                                      ▼
             embed ─▶ cache ─hit─▶ respond
                        │miss
                        ▼
             [decompose] ─▶ retrieve ─▶ confident? ─no, budget left─▶ rewrite | broaden ─┐
                               ▲                                                        │
                               └────────────────────────────────────────────────────────┘
                                      │yes                    │no, budget spent
                                      ▼                       ▼
                                   generate ─▶ verify      abstain (with closest passages)
```

| Step | Behaviour |
|---|---|
| condense | With history and a referential question ("why is that?"), one LLM call rewrites it as standalone |
| route | Small talk gets a canned reply, with no LLM and no retrieval |
| cache | Exact match, then paraphrase match (§6.4); bypassed for follow-ups and personalised requests |
| decompose | Opt-in: multi-part questions split into ≤ 3 sub-questions, each retrieved; confidence = weakest part |
| retrieve | §6.1 |
| reformulate | `rewrite` (LLM paraphrase, new embedding) or `broaden` (2× candidates, filters dropped) |
| generate | Template with labelled sources `[S1] file › section (p. 3)`; streamed token by token |
| verify | `citations`: cited labels must exist and ≥ 50 % of claim sentences must cite (instant). `llm`: judge call |
| abstain | Configured message plus the three closest passages, so the user sees why |

LLM calls per request are capped: condense ≤ 1, decompose ≤ 1, rewrite ≤ max retries, generate 1, judge ≤ 1.

### 6.1 Retrieval (`retrieval.py`)
1. One Qdrant round trip (`query_batch_points`) runs the dense and the BM25 query.
2. Fusion client-side: RRF (`1/(k+rank)`) or DBSF (scores normalised to mean ± 3σ per retriever, summed).
   Client-side fusion keeps each candidate's dense and keyword rank, which the trace shows.
3. Rerank with the cross-encoder (sigmoid of logits → relevance in [0, 1]); without a reranker, a calibrated
   dense-cosine (or BM25) relevance is used. Never fused rank scores (an RRF score of 0.016 is not a probability).
4. Relevance floor, then dedup (exact hash + 5-gram Jaccard), optional extractive compression (keep the first
   sentence and every sentence sharing a keyword with the question), then the token budget (skip, don't stop, on
   oversized chunks).
5. Confidence = top relevance, +0.05 when two of the top three come from the same section.

### 6.2 Prompt and privacy
Sources are delimited and labelled as reference data, not instructions. Redaction (§7) applies to sources sent
to a remote model by default (`auto`); the user's own question is not redacted before the model, only before
logging.

### 6.3 Memory (`memory.py`)
Per session: last N turns (used for condensing) in an LRU of sessions with an idle TTL, bounded in size and count.
The user profile (name, preferences) is set explicitly in the UI and sent with each request; it shapes tone, never
facts, and disables caching for that request.

### 6.4 Answer cache (`cache.py`)
Scope = (knowledge base, index fingerprint, query-config fingerprint), so rebuilds and config changes start fresh.
Each answered question bumps its count; the `capacity` most-asked of the last `history` questions (frequency, then
recency) form the active cache. Lookup: exact normalised match first; then cosine ≥ threshold **and** identical
negations, numbers and named entities ("does it attract pests" ≠ "does it not attract pests"). Abstentions and
failed verifications are never cached.

## 7. Security and privacy

- API binds to 127.0.0.1; a non-loopback bind needs `RAG_API_KEY` (bearer auth on every route except health).
  Containers set `RAG_PUBLIC_BIND_OK` and publish ports on 127.0.0.1 by default.
- Source folders must resolve inside `RAG_DATA_ROOTS`; uploads keep only the base name, allow only configured file
  types, and are size-limited. Chat filters are restricted to an allowlist of payload fields. Query length is
  capped.
- PII detectors are regex candidates confirmed by validators: Luhn (cards), mod-97 (IBAN), IP parsing, phone
  formats (international or national, ISBN excluded), US SSN, PK CNIC, UK NINO. Placeholders are numbered
  consistently per request (`[REDACTED_EMAIL_1]`). Logged queries are always redacted.
- nginx adds CSP, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`. Images run as a non-root user; source mounts
  are read-only; versions are pinned and Python dependencies locked (`uv.lock`).

## 8. Telemetry (`telemetry.py`)

Every request produces a **trace** (persisted at `standard` and `verbose` levels) and one **log line**:

```
chat rid=tr_9f2c kb=kb_1 v=7 outcome=answered total=2.41s ttft=0.62s ret=180ms gen=1.90s
     funnel=20/20>31>9>7>5 conf=0.58 retries=1 verify=pass cache=miss
```

| Trace field | Example |
|---|---|
| outcome, cache hit, verification | answered · miss · pass |
| spans (start, duration, attributes) | embed 40 ms, retrieve 180 ms (funnel, confidence), generate 1.9 s |
| funnel | dense 20 · keyword 20 → fused 31 → reranked 31 → relevant 9 → deduplicated 7 → context 5 (2,310 tokens) |
| decisions | `condensed follow-up`, `retry 1: rewrite (confidence 0.21)`, `cache bypassed: personalised request` |
| top candidates | file, section, page, dense rank, keyword rank, relevance (chunk text only at `verbose`) |
| tokens, time to first token, error (stage, type, message) | |

Build telemetry: per-stage counts, timings and throughput; failures per document; a corpus profile (documents,
pages, OCR'd pages, tables, chunks, chunk-token p50/p95). Span names follow OpenTelemetry conventions so an OTLP
exporter can be added without touching call sites.

### 8.1 The seven observability concerns (`observability.py`, `deploy/observability/`)

| Concern | How |
|---|---|
| Logs | Text or JSON; every line carries `rid` (request or build-job id); one access line per HTTP request with the route template; one summary line per chat request |
| Metrics | Prometheus `/metrics` (OpenMetrics with exemplars on request): HTTP requests and latency by route, chat outcomes, duration, time to first output, retrieval confidence, retries, verification verdicts, errors by stage, LLM tokens, build jobs by status, chatbots, indexed chunks |
| Traces | Per-request trace in SQLite (§8 table); W3C `traceparent` accepted and returned so an outer tracer joins the same trace id |
| Correlation | One id per request: incoming `traceparent` trace id, else a valid `X-Request-ID`, else generated. It is the trace id, the `rid` on log lines, the `X-Request-ID` response header, the exemplar on chat metrics, the id the UI links to, and it is forwarded to model servers |
| SLOs | Availability (99 % of chat requests succeed) and latency (95 % show output within 5 s), configurable via `RAG_SLO_*`, evaluated over 7 days from traces at `/api/slo`, with error budget remaining |
| Alerts | Multi-window burn rates (1 h/5 m and 6 h/30 m page, 3 d/6 h ticket) evaluated in-app at `/api/slo`, plus build-failure alerts; the same rules for Prometheus in `rules.yml` (with RagkitDown, HTTP 5xx and model-server error alerts); `rag up --observability` runs Prometheus |
| Cardinality | Labels only from closed sets (route templates, status classes, outcomes, verdicts, stages); unknown paths collapse to `unmatched`; chatbot, session and job ids and queries never become labels; a test enforces the series budget |

## 9. API

| Route | Purpose |
|---|---|
| `GET /api/system/health · ready · capabilities` | Liveness, dependencies, Qdrant mode |
| `GET /api/schema`, `GET /api/presets/{name}`, `GET /api/models?provider=` | Drive the setup UI |
| `GET/POST /api/kbs`, `GET/DELETE /api/kbs/{id}` | Chatbots |
| `GET/POST /api/kbs/{id}/configs`, `GET …/configs/{v}`, `POST …/configs/validate` | Versions, validation with source and model checks |
| `POST/GET /api/kbs/{id}/builds`, `GET /api/jobs/{id}`, `POST …/cancel`, `GET …/events` (SSE) | Builds |
| `GET/POST /api/kbs/{id}/uploads` | Upload source |
| `POST /api/kbs/{id}/chat` (SSE: status, sources, token, verdict, done, error) | Chat |
| `DELETE /api/kbs/{id}/sessions/{sid}` | Forget a conversation |
| `GET /api/traces`, `GET /api/traces/{id}`, `GET /api/kbs/{id}/stats` | Telemetry |
| `GET /api/kbs/{id}/suggestions` | Starter questions drawn from the indexed sections |
| `GET /api/slo?kb_id=` | SLO status, error budget and firing alerts |
| `GET /metrics` | Prometheus exposition (needs the API key when one is set) |
| `POST /api/kbs/{id}/eval` | Golden-set evaluation for a version |

OpenAPI docs at `/docs`. Streams are SSE over `fetch`, so they work with POST bodies and auth headers.

## 10. Frontend

React 19 + TypeScript (strict) + Vite + Tailwind 4; TanStack Query for server state; native `<dialog>`, `<select>`
and switch-role buttons instead of a component library; CSS bars instead of a chart library. The visual language
("Gallery Labels") is recorded in [DESIGN.md](DESIGN.md): gallery-white walls, an oxblood navigation rail, and the
museum label as the signature for every source. Self-hosted Hanken Grotesk and JetBrains Mono, light and dark
themes from CSS tokens, responsive down to phone width (off-canvas rail). Copy is written for non-technical users.

| Route | Screen |
|---|---|
| `/` | Chatbots: each with its next step (Ask, Continue setup, Watch the build, Rebuild, See what went wrong) |
| `/new` | Guided creation: name, documents (folder or upload), answer style, then create and build |
| `/health` | SLOs in plain words, error budget, anything that needs attention |
| `/kb/:id/ask` | Streaming answers, citation chips, source labels in the margin, "How Archie found this" trace |
| `/kb/:id/setup` | Schema-driven settings grouped by what a change costs, inline validation, presets |
| `/kb/:id/builds[/:job]` | Live build timeline, outcome, technical details, earlier builds |
| `/kb/:id/evaluate` | Test questions, plain-named metrics, runs compared across versions |
| `/kb/:id/activity` | Every question, its outcome, time and confidence, with the full trace |

## 11. Performance (measured on the development laptop, CPU only)

| Measurement | Result |
|---|---|
| PDF parsing, 215-page book | 10.8 s (50 ms/page) with pdfium; 72 s with pdfplumber words; 129 s with de-duplication |
| Full build of two books (245 pages, 364 chunks, fake embeddings) | 21 s |
| Reranking 30 chunks of ~400 tokens | MiniLM-L-6: 2.0 s · bge-reranker-base: 14.4 s |
| Reranker discrimination (relevant / irrelevant) | MiniLM 0.965 / 0.000 · bge 0.979 / 0.000 |
| UI bundle | 130 KB gzipped |

Levers: candidates per retriever (reranking cost is linear), the reranker model, a GPU (`compose.gpu.yaml`, or
an OpenAI-compatible GPU server), server-mode Qdrant with HNSW for large corpora, and the answer cache.

## 12. Deviations, with reasons

| Spec or first draft | As built | Why |
|---|---|---|
| Streamlit UI | React SPA | Requested; streaming, schema forms and trace views need it |
| Every value in the UI | Essentials/Advanced tiers + 3 presets | A UI with every knob is code by another name |
| Docling parser | pdfium + pdfplumber tables (+ optional OCR) | Docling pulls in PyTorch; pdfium is 7× faster than pdfminer here and has a permissive licence (PyMuPDF is AGPL) |
| Semantic (embedding-breakpoint) chunking | Structure-aware + fixed | Embedding every sentence at ingest is slow; structure-aware chunking on headings and paragraphs captures the same boundaries in well-formatted documents |
| Server-side fusion (Qdrant FusionQuery) | Client-side RRF/DBSF over one batched query | Same round trips, and it keeps per-retriever ranks for the trace |
| bge-reranker-base default | MiniLM-L-6 default; bge in `high_accuracy` | Measured 7× faster on CPU with near-identical discrimination (§11) |
| Laptop/GPU hardware presets | Three quality presets; GPU via compose override | Hardware is infrastructure, not chatbot config |
| Neighbour merging of adjacent chunks | Not built | Overlap already covers adjacency; merging duplicated overlap text |
| OpenTelemetry SDK + exporter | OTel-shaped spans in SQLite; exporter not built yet | No extra dependency until someone needs OTLP |
| Generated TypeScript client | Hand-written typed client (`lib/api.ts`) | Most responses are dynamic dicts; generated types would add little |
| Implicit capture of the user's name | Explicit profile in the UI | Implicit extraction is unreliable and surprising |
| Person-name redaction (NER) | Not built (validated pattern detectors only) | Needs a model dependency; add Presidio as a detector when required |
| Self-validation by an LLM | Citation check by default, LLM judge opt-in | Doubles latency on a local 9B model for modest gain |
| Redact PII before every LLM call | Logs always; prompt sources when the model is remote | Redacting context for a local model hurts answers and protects nothing |
| Pure semantic answer cache | Exact + guarded paraphrase cache, scoped and bypassed when personalised | Embedding-only hits return confidently wrong answers |
| Wait for ingestion before chatting | Build into a new collection, swap atomically | No downtime on rebuilds |
