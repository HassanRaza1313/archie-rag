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
  providers.py    Ollama, OpenAI-compatible, Claude (official SDK); FastEmbed embeddings and reranker
  connections.py  model connections added in the app; keys encrypted at rest
  sources.py      data sources (local folder, uploads, website) with the data-root allowlist
  web.py          website source: crawls one site into a cache, refusing private addresses
  connectors/     S3, databases, Confluence, Jira, SharePoint/OneDrive, Google Drive and Notion, read through accounts
  injection.py    flags wording aimed at an AI (prompt injection) in indexed passages
  parsing.py      PDF (pdfium text + pdfplumber tables + optional OCR), Markdown, text
  chunking.py     structure-aware and fixed chunking
  textutil.py     token estimate, sentences, BM25 terms, shingles
  index.py        Qdrant collections, upserts, batched dense + keyword search
  retrieval.py    fusion, rerank, relevance floor, dedup, compression, token budget
  privacy.py      validated PII detectors and consistent redaction
  cache.py        answer cache: exact + guarded paraphrase match, frequency promotion
  backup.py       one-file backup and restore of the database and uploads
  agent.py        the bounded agent (state machine) that answers a chat request
  router.py       picks which source groups (other chatbots) a question searches
  claims.py       checks each answer sentence against its cited passage (local entailment model)
  telemetry.py    request traces, one-line request logs, JSON logging
  jobs.py         build pipeline and worker loop
  evaluation.py   golden-set retrieval evaluation; drafts golden sets and re-runs them after changes
  api.py          HTTP API + SSE + static UI
  cli.py          ragkit serve | worker | kb | eval | backup | restore | doctor
backend/tests/    227 tests: unit, integration (embedded Qdrant), agent scenarios, API, CLI
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
| Source ⟳ | type (local folder ★ / uploads), folder, file types (.pdf .md .txt .html .docx .pptx .xlsx) | include/exclude globs, recursive |
| Parsing ⟳ | reader (auto ★ / fast / layout), OCR (auto ★ / off / force), tables (markdown ★ / off) | strip running headers/footers |
| Chunking ⟳ | strategy (auto ★ / structure / sections / slides / rows / fixed), size 512 tokens, overlap 12 % | section path in embedding text |
| Enrichment ⟳ | context per chunk, likely questions, keywords (all off ★), provider, model | document excerpt 3000 tokens, 4 parallel calls |
| Embedding ⟳ | provider (ollama ★ / openai / fastembed), model (nomic-embed-text) | batch size 32 |
| Index ⟳ | – | HNSW m 16, ef_construct 128, int8 quantization |
| Retrieval | mode (hybrid ★ / dense / keyword), candidates 20 | search ef 64, filterable fields |
| Fusion | rrf ★ / dbsf | RRF k 60 |
| Reranking | enabled, model (MiniLM-L-6 ★), final chunks 6 | minimum relevance 0.2 |
| Context | token budget 3000, dedup on, surrounding text (neighbors ★ / section / off) | near-duplicate threshold 0.85, compression (off ★ / extractive) |
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

`validate → provision → discover → parse → chunk → enrich → embed → index → reconcile → smoke_test → activate`

- **validate**: source has supported files; the embedding model answers a probe (its dimension sizes the index).
- **provision**: collection `kb_<id>__<index-fingerprint>` with a named dense vector (cosine, HNSW, optional int8)
  and a BM25 sparse vector (`modifier=idf`); payload indexes in server mode.
- **discover**: SHA-256 per file; files whose hash is already indexed in this collection are skipped. This makes
  rebuilds incremental and interrupted builds resumable.
- **parse**: Markdown, text and HTML in a thread; everything else in worker processes, each file under a time limit
  (`RAG_PARSE_TIMEOUT_S`, 600 s) and on Linux/macOS a memory limit (`RAG_PARSE_MEMORY_MB`). A file that hangs is
  skipped and the pool restarted; when a worker crashes, the files that were running are retried one at a time so
  only the culprit fails; if worker processes cannot run at all on the host, parsing falls back to in-process.
- **chunk**: hand edits from the Documents page are applied (while the file is unchanged); passages identical to
  one already indexed from another file in this build are skipped and counted.
- **enrich** (optional): see 5.3.
- **embed / index**: batched embeddings; deterministic point ids `uuid5(kb, content_hash, ordinal)`; a changed
  document's old points are deleted before its new ones are written. Each document gets a report (reader,
  template, layout hints, warnings, duplicates, edits, a 0–1 parse-quality score) shown on the Documents page.
- **reconcile**: deletes documents removed from the source.
- **smoke_test**: three searches built from indexed chunks must return results.
- **activate**: atomically points the chatbot at the new collection and drops the previous one. Chat keeps using
  the old index until this moment, so rebuilds have no downtime.

Honest statuses: `completed`, `completed_with_errors` (some documents failed; listed with stage and reason),
`failed` (nothing indexed, smoke test failed, or a fatal error), `cancelled`. Per-stage counts (in, out, skipped,
failed), timings and throughput are recorded and streamed to the UI.

### 5.1 Parsing (`parsing.py`, `layout.py`)
Two readers. The **fast reader** is described below. The **layout reader** (`layout.py`, the optional `layout`
extra) is Docling (MIT): a page-layout model classifies titles, headings, paragraphs, lists, tables, captions and
page furniture; TableFormer rebuilds table structure; scanned pages are OCR'd; DOCX, PPTX and XLSX are read
natively (PowerPoint speaker notes are added per slide). Docling's output maps onto the same blocks, so chunking
and everything after it is unchanged.

`reader: auto` (default) sends Office files to the layout reader, and PDFs to it only when the fast reader flags
them: a page where at least a quarter of the lines contain a gap wider than four font sizes (text columns, or a
borderless table), or scanned pages. Simple PDFs stay on the fast reader, which is about 100× faster on CPU. If the
layout extra is missing or fails on a file, the fast result is kept with a warning. HTML always uses a built-in
reader (stdlib), which skips navigation, scripts and other page chrome.

The fast reader:
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

Templates are presets over the structure chunker for shapes where its defaults hurt: `sections` never merges small
sections (FAQs, numbered clauses), `slides` splits at every slide or page, `rows` cuts tables into small row groups
with the header repeated (spreadsheets). `auto` (default) picks per document: `rows` for spreadsheets, `slides`
for presentations, `sections` when most headings are questions or numbered clauses, the structure chunker without
the references list for papers (Abstract + References headings), and `structure` otherwise.

### 5.3 Enrichment (`enrichment.py`)
Optional and off by default because it needs a chat model at build time. For each chunk the model sees the
document (or its opening plus the text around the chunk, within `document_tokens`) and returns JSON with any of:
a sentence situating the chunk in its document (contextual retrieval), up to three questions it answers, and up to
six keywords including synonyms. These are prepended to the chunk's embedded and BM25 text, never to the text
shown or sent to the answering model. Results are cached by content, model and prompt version, so rebuilding
unchanged documents makes no calls; a malformed reply leaves that chunk unenriched rather than failing the build.
Validation warns about the cost (one call per chunk, estimated from the last build).

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
| groups | With `retrieval.groups`, the router (§6.5) picks which chatbots' indexes to search |
| decompose | Opt-in: multi-part questions split into ≤ 3 sub-questions, each retrieved; confidence = weakest part |
| retrieve | §6.1 |
| reformulate | `rewrite` (LLM paraphrase, new embedding) or `broaden` (2× candidates, filters dropped) |
| generate | Template with labelled sources `[S1] file › section (p. 3)`; streamed token by token |
| verify | `claims` (default): each sentence checked against its cited passage by a local entailment model (§6.6). `citations`: cited labels must exist and ≥ 50 % of claim sentences must cite (instant). `llm`: judge call |
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
5. Small-to-big expansion: each selected chunk is widened with its neighbouring chunks (or its whole section),
   best hit first, while the token budget allows; the chunker's repeated overlap is removed when joining.
6. Confidence = top relevance, +0.05 when two of the top three come from the same section.

### 6.2 Prompt and privacy
Sources are delimited and labelled as reference data, not instructions. Redaction (§7) applies to sources sent
to a remote model by default (`auto`); the user's own question is not redacted before the model, only before
logging.

### 6.3 Memory
Per session: the last turns (used for condensing), stored in the database so every API process shares them; at most
50 kept per session, turns older than 12 hours forgotten, expired ones purged by the worker, and erased with the
chatbot.
The user profile (name, preferences) is set explicitly in the UI and sent with each request; it shapes tone, never
facts, and disables caching for that request.

### 6.4 Answer cache (`cache.py`)
Scope = (knowledge base, index fingerprint, query-config fingerprint), so rebuilds and config changes start fresh.
Each answered question bumps its count; the `capacity` most-asked of the last `history` questions (frequency, then
recency) form the active cache. Lookup: exact normalised match first; then cosine ≥ threshold **and** identical
negations, numbers and named entities ("does it attract pests" ≠ "does it not attract pests"). Abstentions and
failed verifications are never cached.

### 6.5 Source groups and the router (`router.py`)
A chatbot may also search other chatbots (`retrieval.groups`). Each group keeps its own index and its built
version's search settings (search model, mode, fusion); ranking, context and generation follow the asking chatbot,
so one cross-encoder scores every group's passages on the same scale. Per question:

1. A question that names a group goes to it.
2. Otherwise naive Bayes over each group's vocabulary (in how many of up to 2,000 sampled passages each word
   appears, with every group counted as at least 50 passages) gives each group a probability. Groups are added,
   most likely first, until they reach 90 % (at most 3). Words found in no group are ignored; none known means all.
3. Each chosen group is searched in parallel; per question the best group sets the confidence, and results merge
   by relevance before the token budget. The `broaden` retry searches every group.

Profiles are built lazily per collection and kept in memory per process. The answer cache scope includes every
group's index, so a rebuilt group never serves stale answers. A chatbot with groups and no files of its own builds
an empty index and answers only from its groups.

### 6.6 Claim check (`claims.py`)
The default answer check (`agent.verification: claims`) checks each sentence against the passage it cites with a
small local entailment model (DeBERTa-v3-xsmall trained on MNLI, FEVER, ANLI and LingNLI, MIT licence; int8 ONNX,
87 MB, CPU, no PyTorch). It is downloaded into the model cache by the first build that uses it.

1. The answer is split into sentences per paragraph. Questions, "I don't know" and fragments are skipped. A
   sentence without its own citation borrows the next cited sentence's in its paragraph, else the previous one's,
   else every source the answer cites.
2. Each sentence (citations and markup removed) is scored against every 200-word window, with a 150-word stride, of
   each passage it cites; its support is the best entailment probability. At 0.5 or above it counts as backed.
3. The answer passes when at least half its sentences are backed. Unbacked sentences are marked in the answer,
   and the share backed is the `support` attribute of the trace's `verify` span.

A cited label that does not exist, or an answer with no citations, falls back to the citation check. So does a
missing model, with a note in the trace. Three sentences take about 0.1 s on a laptop CPU. A small NLI model misses
paraphrase that needs inference, so a flag means "check this", not "false"; `on_verify_fail: abstain` replaces an
answer only when most of it is unbacked.

### 6.7 Golden sets drafted for you (`evaluation.py`)
Each chatbot keeps one golden set on the server (the Quality page saves edits as you type), and the last 20 runs
with what triggered each one.

- **Drafting** reads up to 2,000 indexed passages, drops excluded and short ones (under 200 characters), and picks
  up to `count` (default 10) spread evenly through every document, round-robin across documents. Each becomes one
  question naming its file: enrichment's first question for the passage when there is one, else one call to the
  chatbot's model, asked to use its own words. One more call drafts two questions the documents can't answer. Drafts
  are only suggestions, marked as drafted until edited; nothing is saved until the set is.
- **Re-runs** follow every successful build, and every saved settings change that needs no rebuild (one that does
  re-runs after its build). A run takes one embedding per question and no LLM calls.
- **What changed:** per question, a pass is the expected file in the top 5 without declining (or a decline, for
  questions without one). The page marks each question that passed in the previous run and fails now, or the
  reverse.

Passages sent to a remote model while drafting are redacted as for answers (`privacy.redact_prompt`).

### 6.8 Connectors (`connectors/`)
A connector lists the items at a `location` in an account and saves each one into `state/sources/<chatbot>/`. A
manifest keeps each item's version, so a sync downloads only new and changed items and deletes those that are
gone; the build then reads the cache like a folder, the item's address being its `uri` (so citations show it).

| Source | Account | Location | Version | Fields kept |
|---|---|---|---|---|
| S3 (boto3; MinIO, R2, Wasabi via an endpoint) | access key | `bucket/prefix/` | ETag + size | modified |
| Database (SQLAlchemy; PostgreSQL, MySQL, SQLite, others with a driver) | URL | one `SELECT` | row hash | every short column |
| Confluence (Cloud REST v2, Data Center v1) | Atlassian | space keys | page version | space, updated |
| Jira (Cloud enhanced JQL search, Data Center v2) | Atlassian | JQL | issue hash | key, status, type, priority, people, labels, dates |
| SharePoint and OneDrive (Microsoft Graph, app-only) | Microsoft 365 | folder address or `person@company/Folder` | cTag | modified, modified by, folder |
| Google Drive (service account, RS256 JWT) | Google | folder address | file version | modified, modified by |
| Notion (API 2022-06-28) | Notion | page or database address, or empty | last edited | database properties |

Docs, Sheets and Slides export as Markdown, CSV and text; Jira's rich text (ADF) and Notion blocks become
Markdown; a Confluence page is its storage-format HTML. Database rows: the first column is the id, a title, name or
subject column titles it, values up to 200 characters become fields (`fields.<column>` on every passage, filterable
when listed in `retrieval.filter_fields`) and longer text the body. A SQLite file appears in citations by its name
only. Calls back off on 429/503 (Retry-After, up to 3 times); 401/403/404 become messages people can act on. At
most `source.max_pages` items are read per sync.

Accounts (`source_accounts`): owners add them (`POST /api/source-accounts`), each checked live before it is saved;
non-secret settings are stored as JSON, secrets encrypted (Fernet, as model keys) and never returned. A chatbot's
config names only the account id, so viewers who can read the config never see a secret. An account in use can't be
removed. Validation before a build only checks the account choice and the location; the service is read by the build.

## 7. Security and privacy

- API binds to 127.0.0.1; a non-loopback bind needs `RAG_API_KEY` (bearer auth on every route except health).
- Scoped API keys (H2): `rk_` plus 32 random bytes, stored as a SHA-256 hash with the first 10 characters for
  recognition; scope `ask` acts as a viewer and `manage` as an editor (never an owner), optionally limited to one
  chatbot, whose key then reaches only `/api/kbs/<id>…` and read-only system routes (`accounts.key_allows`). Only
  owners make or revoke keys; keys of a deleted chatbot go with it; last use is recorded at most once a minute. A
  bearer token that matches nothing is refused (401) unless a signed-in session comes with it.
  Containers set `RAG_PUBLIC_BIND_OK` and publish ports on 127.0.0.1 by default.
- Source folders must resolve inside `RAG_DATA_ROOTS`; uploads keep only the base name, allow only configured file
  types, and are size-limited. Chat filters are restricted to an allowlist of payload fields. Query length is
  capped.
- Connectors read only. Databases: one `SELECT` (or `WITH ... SELECT`) is accepted, write keywords are refused, the
  session is set read-only where the database allows it (PostgreSQL, MySQL, SQLite `mode=ro`), a PostgreSQL
  statement times out after 120 s, and every transaction is rolled back; a read-only database user is still
  advised, because a `SELECT` can call functions with side effects. SharePoint downloads use Graph's
  pre-authorised links, so the bearer token never leaves Graph. Content from every service is screened like web
  pages (C4). Deleting a chatbot deletes its downloaded copies.
- PII detectors are regex candidates confirmed by validators: Luhn (cards), mod-97 (IBAN), IP parsing, phone
  formats (international or national, ISBN excluded), US SSN, PK CNIC, UK NINO. Placeholders are numbered
  consistently per request (`[REDACTED_EMAIL_1]`). Logged queries are always redacted.
- Website sources (`web.py`): http(s) only; every request and redirect is refused when the host resolves to
  loopback, private, link-local or other non-public addresses (cloud metadata included) unless
  `RAG_WEB_ALLOW_PRIVATE` is set for an intranet; redirects may not leave the site; 20 MB per page; robots.txt and
  its crawl delay (up to 5 s) honoured. Known limit: the name is resolved again on connect, so DNS rebinding is
  not prevented; sources are editable only by editors.
- Prompt injection (C4, `injection.py`): text hidden on a page (`hidden`, `aria-hidden`, `display:none`,
  `visibility:hidden`, zero font size) is never read. Passages with wording aimed at an AI ("ignore all previous
  instructions", "note to the AI", chat-template tokens) are flagged on the Documents page; from websites those
  sentences are removed before indexing (the passage is left out only when nothing else remains), and an owner's
  edit or "Use in search" always wins. The system prompt tells the model that sources are material, not
  instructions.
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
| Feedback and gaps | Thumbs up or down under every answer, on shared pages too, stored on its trace (taken back by pressing again). Status lists the 10 questions of the last 30 days most often abstained on or rated not helpful, per chatbot, grouped ignoring case, spacing and end punctuation: what the documents lack. Ratings go with their traces under trace retention |
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
| `POST /api/kbs/{id}/share` `{enabled, reset}` | Turn the share link on or off, or replace it |
| `GET /api/auth/me`, `POST /api/auth/owner`, `…/sign-in`, `…/sign-out`, `…/password`, `GET/POST /api/auth/invite/{token}` | Accounts: who is signed in, the first owner, sessions, invitations and password resets |
| `GET/POST /api/users`, `PATCH/DELETE /api/users/{id}`, `POST /api/users/{id}/invite`, `GET /api/audit` | People and the change history (owners only) |
| `GET/POST /api/keys` `{name, kb_id, scope}`, `DELETE /api/keys/{id}` | Scoped API keys (owners only); the key is returned once, on creation |
| `GET/POST /api/source-accounts`, `DELETE /api/source-accounts/{id}` | Connector accounts: owners add (checked live) and remove; editors list |
| `GET /api/share/{token}`, `…/suggestions`, `POST …/chat`, `POST …/feedback`, `DELETE …/sessions/{sid}` | Shared chatbot: no admin key, rate-limited per visitor |
| `POST /api/kbs/{id}/feedback` `{trace_id, rating: up·down·null}`, `GET /api/gaps?days=` | Rate an answer (viewers may); the most asked unanswered or unhelpful questions (editors) |
| `GET /api/system/folders?path=` | Folder picker: one level inside the allowed data roots, with supported-file counts |
| `GET /api/setup/models?preset=` | Whether a preset's models are installed; Archie's built-in search model as a fallback |
| `DELETE /api/kbs/{id}/sessions/{sid}` | Forget a conversation |
| `GET /api/traces`, `GET /api/traces/{id}`, `GET /api/kbs/{id}/stats` | Telemetry |
| `GET /api/kbs/{id}/suggestions` | Starter questions drawn from the indexed sections |
| `GET /api/kbs/{id}/documents`, `GET …/documents/chunks?uri=` | Per-document reports; a document's chunks in order |
| `PATCH …/documents/chunks`, `DELETE …/documents/chunks?uri=&ordinal=` | Edit or exclude a chunk (re-embedded at once); undo |
| `GET /api/slo?kb_id=` | SLO status, error budget and firing alerts |
| `GET /metrics` | Prometheus exposition (needs the API key when one is set) |
| `POST /api/kbs/{id}/eval`, `GET …/eval/runs` | Golden-set evaluation for a version, kept with what triggered it (last 20 runs) |
| `GET/PUT /api/kbs/{id}/golden`, `POST …/golden/draft` `{count}` | The chatbot's saved test questions; drafted ones to review |

OpenAPI docs at `/docs`. Streams are SSE over `fetch`, so they work with POST bodies and auth headers.

## 10. Frontend

React 19 + TypeScript (strict) + Vite + Tailwind 4; TanStack Query for server state; native `<dialog>`, `<select>`
and switch-role buttons instead of a component library; CSS bars instead of a chart library. The visual language,
recorded in [DESIGN.md](DESIGN.md), is a Mac app for your documents: platform type (SF Pro, Segoe UI Variable),
a translucent source-list sidebar of chatbots, inset grouped sections, segmented controls and sheets, one accent
(Archie wine) for actions and selection only, and Quick Look for sources. Light and dark themes (or follow the
system) from CSS tokens, responsive down to phone width (the sidebar slides in), reduced motion keeps only fades.
Copy is written for non-technical users. Each chatbot has two places: **Ask**, and **Manage** with five tabs.

| Route | Screen |
|---|---|
| `/` | Welcome and Continue on first run; afterwards opens the chatbot used last |
| `/new` | Setup assistant, one decision per screen: name, documents (folder picker or upload), answer style with a live model check; Create builds and opens Ask |
| `/status` | SLOs in plain words, error budget, anything that needs attention |
| `/kb/:id/ask` | Build progress until the first build finishes, then suggested questions, streaming answers, citation chips, source cards, Quick Look, "How Archie found this" trace; Share sheet (link and website embed) |
| `/people` | Owners: people, their access (can ask, can edit, owner), invitation and password links, the change history; on an open install, turning sign-in on |
| `/invite/:token` | Join with an invitation, or choose a new password from a reset link |
| `/share/:token` | The shared chatbot alone, for anyone with the link; also the page inside the website widget (`/widget.js`) |
| `/kb/:id/manage/documents` | How each file was read and split, files most likely misread first; edit or exclude passages |
| `/kb/:id/manage/settings` | Schema-driven settings grouped by what a change costs, inline validation, presets, delete chatbot |
| `/kb/:id/manage/builds[/:job]` | Live build steps, outcome, technical details, history |
| `/kb/:id/manage/quality` | Test questions, plain-named results, runs compared across versions |
| `/kb/:id/manage/activity` | Every question, its outcome, time and best match, with the full trace |

Older links (`/health`, `/kb/:id/setup`, `/builds`, `/evaluate`, `/activity`, `/documents`, `/chat`) redirect.

## 11. Performance (measured on the development laptop, CPU only)

| Measurement | Result |
|---|---|
| PDF parsing, 215-page book | 10.8 s (50 ms/page) with pdfium; 72 s with pdfplumber words; 129 s with de-duplication |
| Full build of two books (245 pages, 364 chunks, fake embeddings) | 21 s |
| Reranking 30 chunks of ~400 tokens | MiniLM-L-6: 2.0 s · bge-reranker-base: 14.4 s |
| Reranker discrimination (relevant / irrelevant) | MiniLM 0.965 / 0.000 · bge 0.979 / 0.000 |
| UI bundle | 130 KB gzipped |
| Ingestion benchmark (`bench/`, 9 hard documents, 30 questions) | passage found first: 63 % with the original pipeline, 96 % with the new defaults (setup.md) |

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
| Self-validation by an LLM | Local per-sentence claim check by default, LLM judge opt-in | A judge call doubles latency on a local 9B model; a small NLI model checks each sentence in a fraction of a second |
| Redact PII before every LLM call | Logs always; prompt sources when the model is remote | Redacting context for a local model hurts answers and protects nothing |
| Pure semantic answer cache | Exact + guarded paraphrase cache, scoped and bypassed when personalised | Embedding-only hits return confidently wrong answers |
| Wait for ingestion before chatting | Build into a new collection, swap atomically | No downtime on rebuilds |
