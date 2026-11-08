# Setup

> Companion to [plan.md](plan.md) and [architecture.md](architecture.md). Every command below exists in
> `scripts/rag.sh` (macOS, Linux, Git Bash) and `scripts/rag.ps1` (Windows). PowerShell spells flags as
> `-WithOllama`, `-Gpu`, `-Release`, `-Build`, `-Volumes`, `-Yes`.

## 1. Choose how to run it

| Mode | Command | Good for | Needs |
|---|---|---|---|
| **Docker, split services** ★ | `rag up` | Using it on any machine | Docker with Compose v2 |
| Docker + bundled Ollama | `rag up --with-ollama` (`--gpu` for NVIDIA) | Machines without Ollama | Docker (+ NVIDIA toolkit) |
| Prebuilt images | `rag up --release` | Servers, pinned versions | Docker; `RAG_IMAGE_OWNER` in `.env` |
| **All-in-one container** | see §4 | Quickest try-out | Docker |
| **Local development** | `rag dev` | Changing the code (hot reload) | Python 3.10+ (or uv), Node 22.12+ |
| Local, no Docker | `rag run` | Machines where Docker is not allowed | Python 3.10+, Node 22.12+ |

Model servers are never inside the app image. Use Ollama on the host (best on macOS, where Docker has no GPU),
the optional Ollama container, or any OpenAI-compatible server (LM Studio, vLLM, llama.cpp, OpenAI). Every
component is addressed by URL in `.env`, so mixing works: for example `rag dev` with Qdrant in Docker and Ollama on
a GPU box.

## 2. Quick start

```bash
git clone https://github.com/HassanRaza1313/archie-rag.git && cd archie-rag
./scripts/rag.sh setup          # Windows: .\scripts\rag.ps1 setup   (or double-click scripts\start-windows.bat)
./scripts/rag.sh up             # or: ./scripts/rag.sh dev  for local development
```

Open http://localhost:8080 (Docker) or http://localhost:5173 (dev), then:
**Create your first chatbot** → name it → keep the sample folder and *Balanced* → **Create and build** → **Ask your first question**.

`setup` is idempotent. It creates `.env` from `.env.example`, installs the backend (with `uv sync --frozen` when uv
is available, otherwise a virtualenv and pip), installs the frontend when Node is present, and offers to pull the
default models into Ollama (`--yes` accepts).

Default models: chat `qwen3.5:9b`, embeddings `nomic-embed-text` (both via Ollama). The reranker
(`ms-marco-MiniLM-L-6-v2`, ~90 MB) downloads automatically on first use into `.ragkit/models` (Docker: the
`model_cache` volume).

## 3. Commands

| Command | What it does |
|---|---|
| `setup [--yes]` | Dependencies, `.env`, optional model pull |
| `doctor` | Checks Python, data roots, Qdrant, Ollama and the default models, reranker, Node, Docker, free port |
| `dev` | API with auto-reload (+ in-process worker) and the Vite dev server; one terminal, Ctrl-C stops both |
| `run` | Builds the UI once, then serves UI + API + worker from one local process |
| `up [--with-ollama] [--gpu] [--release] [--build]` | Starts the Docker stack and waits for health checks |
| `down [--volumes]` | Stops it (`--volumes` deletes all data, after confirmation) |
| `build [--tag X] [--push]` | Builds the `api`, `ui` and `aio` images (`--push`: multi-arch with buildx) |
| `logs [service]`, `status` | Container logs and state |
| `models pull` | Pulls `RAG_PULL_MODELS` with host Ollama, or with the Ollama container if Ollama isn't installed |
| `kb list`, `kb build NAME --config kb.yaml [--preset P]`, `kb export NAME` | Headless chatbot management (CI, servers) |
| `eval NAME golden.jsonl [--version V]` | Retrieval evaluation from the terminal |
| `reset` | Deletes local state in `.ragkit` (after confirmation) |

Headless example (`kb.yaml` is merged over the preset):

```yaml
source: { path: data/sources/sample }
chunking: { chunk_size: 400 }
rerank: { top_n: 4 }
```

## 4. Containers

One Dockerfile (`deploy/docker/Dockerfile`), three targets:

| Target / service | Contents | Port |
|---|---|---|
| `api` | FastAPI app; the same image runs the `worker` service (`ragkit worker`) | 8000 |
| `ui` | Built SPA on unprivileged nginx; proxies `/api` to `RAG_API_UPSTREAM` with buffering off for streams | 8080 |
| `aio` | API + worker + UI + embedded Qdrant in one container | 8000 |
| `qdrant/qdrant:v1.20.0` | Vector database | internal |
| `ollama/ollama:0.40.11` (profile `ollama`) | Model server, plus a one-shot `ollama-init` that pulls models | internal |

All-in-one, no clone needed once images are published:

```bash
docker run -d -p 127.0.0.1:8000:8000 \
  -v "$PWD/my-docs:/data/sources:ro" -v ragkit_state:/data/state -v ragkit_models:/data/models \
  -e RAG_OLLAMA_URL=http://host.docker.internal:11434 --add-host host.docker.internal:host-gateway \
  ghcr.io/hassanraza1313/ragkit-aio:latest
```

Volumes (split setup):

| Volume | Mounted at | Holds |
|---|---|---|
| `${RAG_SOURCES_HOST_DIR:-./data/sources}` (read-only bind) | `/data/sources` | Your documents |
| `ragkit_state` | `/data/state` | SQLite (chatbots, versions, jobs, traces, cache), uploads |
| `qdrant_data` | `/qdrant/storage` | Vectors |
| `model_cache` | `/data/models` | Reranker model files |
| `ollama_models` | `/root/.ollama` | Ollama models (profile `ollama`) |

**Your documents in Docker:** containers only see mounted folders. Put files under `data/sources/`, set
`RAG_SOURCES_HOST_DIR=/path/to/docs` in `.env`, or use the **Uploads** source type in the UI. In the chatbot's
Source stage use the container path, for example `/data/sources/my-folder`.

**Host Ollama from containers:** the default `RAG_OLLAMA_URL=http://host.docker.internal:11434` works on Docker
Desktop; on Linux the compose file maps `host-gateway`, and Ollama must listen beyond localhost
(`OLLAMA_HOST=0.0.0.0`).

Image practices: multi-stage builds, locked dependencies (`uv.lock`, `package-lock.json`), pinned base images,
non-root user, health checks, `depends_on: service_healthy`, ONNX reranker (no PyTorch), OCR optional
(`RAG_INSTALL_OCR=true` at build time), layout reader optional (`RAG_INSTALL_LAYOUT=true`: CPU-only PyTorch, about
1.5 GB more).

### Serving a chatbot to other people
1. Point a domain at the server and open ports 80 and 443.
2. In `.env`, set `RAG_DOMAIN=chat.example.com` and a long random `RAG_API_KEY` (this is the admin key; keep it to
   yourself).
3. `docker compose -f deploy/compose.yaml --env-file .env --profile https up -d`. Caddy gets and renews the
   certificate; Archie is at `https://chat.example.com`.
4. In Ask, press **Share** and switch on **Anyone with the link can ask**. Send the link, or paste the embed snippet
   into your website: a chat button appears in the corner and opens the chatbot in a panel.

People with the link can only ask that one chatbot questions (`/api/share/<token>/…`); settings, documents, other
chatbots and the activity log still need the admin key. Each visitor may ask `RAG_SHARE_RATE_PER_MINUTE` questions
a minute (default 20). **Reset Link** in the Share sheet stops the old link and every embed at once. Shared pages may
be framed by any site; every other page refuses framing.

### People and sign-in
A fresh install is open to whoever can reach it (it listens on 127.0.0.1 only). To require sign-in, open **People**
and create the owner account; from then on everyone signs in. Add people with **Add Person**: Archie makes an
invitation link (one use, valid a week) to send them. Access levels:

| Access | Can |
|---|---|
| Can ask | Ask chatbots questions |
| Can edit | Also create, configure, build and share chatbots, and read activity and status |
| Owner | Also manage people, read the change history and delete chatbots |

Passwords are hashed with scrypt; sessions are HttpOnly, SameSite=Strict cookies stored server-side (only their
hash), lasting 14 days. Sign-in is limited to 10 attempts a minute per address, and failed attempts appear in the
change history. A forgotten password: an owner makes a **New Password Link**. `RAG_API_KEY` keeps working as an
owner-level key for scripts and monitoring, and must be supplied to create the first owner when it is set.

### Optional readers
| Extra | Adds | Install locally |
|---|---|---|
| `ocr` | RapidOCR for scanned PDF pages in the fast reader | `pip install -e 'backend[ocr]'` |
| `layout` | Docling layout reader: text columns, borderless tables, scans, Word, PowerPoint, Excel | `pip install -e 'backend[layout]'` |

Without `layout`, Office files are skipped (validation says so) and complex PDFs use the fast reader with a
warning. With it, `reader: auto` sends only the files that need it to Docling, so simple PDFs stay fast.

### Measuring ingestion
`bench/` holds a synthetic, license-clean corpus generator and a golden question set that stress parsing (two text
columns, a borderless table, a scan, numbered clauses, a paper with references, Word, PowerPoint, Excel, HTML).
`bench/run.py` builds it under several configs with local FastEmbed models and prints passage-level hit rates:

```bash
pip install -e 'backend[rerank,ocr,layout,bench]'
python bench/make_corpus.py
python bench/run.py
```

Results on the development laptop:

| Config | Passage found first | Passage in top 5 | File found first | Files failed | Passages | Build |
|---|---|---|---|---|---|---|
| Original pipeline (fast reader, structure chunking) | 63 % | 63 % | 70 % | 3 of 9 | 8 | 13 s |
| Layout reader for everything | 96 % | 100 % | 100 % | 0 | 15 | 63 s |
| `reader: auto` | 96 % | 100 % | 100 % | 0 | 15 | 56 s |
| `reader: auto` + `strategy: auto` (new defaults) | 96 % | 100 % | 100 % | 0 | 51 | 59 s |

27 answerable questions and 3 unanswerable ones (all correctly declined in every config); FastEmbed
`bge-small-en-v1.5` embeddings and the MiniLM reranker, CPU only, first build including model loading. A passage
counts as found when one retrieved chunk contains every expected phrase. The original pipeline misses the Office
files outright and misreads the two-column page. Templates do not change hit rates on documents this short, but cut
each document into passages of its own shape (median 56 tokens instead of whole documents), so the answering model
gets less unrelated text. Enrichment is not in this table: it needs a chat model at build time.

## 5. Configuration files

| File | Holds | Committed |
|---|---|---|
| `.env` | Infrastructure: URLs, ports, Qdrant mode, data roots, API key, logging | No (`.env.example` is) |
| Chatbot configs | Created in the UI or with `rag kb build`, versioned in SQLite, exported with `rag kb export` | Optional |
| `data/eval/*.jsonl` | Golden sets: `{"question": …, "expected_sources": ["file.md"]}` (empty list = should abstain) | Yes |

## 6. Security defaults

- Services bind to 127.0.0.1 and Docker publishes on 127.0.0.1. To expose them, set `RAG_PUBLISH_HOST`
  **and** `RAG_API_KEY`. A local non-loopback bind without a key is refused.
- Source folders must be inside `RAG_DATA_ROOTS`; the setup assistant's folder picker only lists those.
- Share links are random 24-character tokens that allow questions to one chatbot only, rate-limited per visitor.
  Behind the HTTPS profile, the visitor's address comes from the proxy chain (nginx believes `X-Forwarded-For` only
  from private networks). Deleting a chatbot deletes its uploads and cached answers.
- Never commit `.env` or documents other than the sample corpus (`data/sources/*` is git-ignored).

## 7. Verification status

| Path | Status |
|---|---|
| Backend: lint, format, strict-ish mypy, 156 tests (95 % line coverage) incl. embedded Qdrant, agent scenarios, API, CLI | Verified on Windows / Python 3.10 |
| Frontend: Biome, strict TypeScript, 17 tests, production build | Verified (Node 22.15) |
| Full UI flow in a browser (create → build → ask → trace → setup → activity → health → evaluate; desktop + phone, light + dark) | Verified against the real API with deterministic fake models |
| Real FastEmbed rerankers (MiniLM, bge) | Verified, benchmarked |
| Real Ollama models end to end | Not run here (no Ollama on the build machine); covered by provider contract tests |
| Docker images and compose stack | Not run here (no Docker on the build machine); CI builds every target and smoke-tests the all-in-one image |
| `rag.sh` / `rag.ps1` | Syntax-checked; `help` and `doctor` run; `up`/`dev`/`run` not exercised here |

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `doctor`: Ollama unreachable from containers | See "Host Ollama" above, or `rag up --with-ollama` |
| Validate: model not available | `rag models pull`, or choose an installed model (the field suggests them) |
| Slow answers on CPU | Preset *Low latency*, fewer candidates, or a GPU model server |
| Build warns "pages without a text layer were skipped" | Scanned PDF: rebuild the image with `RAG_INSTALL_OCR=true` or `pip install 'ragkit[ocr]'` |
| Validation says Word, PowerPoint or Excel files will be skipped | Install the layout reader: `RAG_INSTALL_LAYOUT=true` (Docker) or `pip install 'ragkit[layout]'`, and keep the reader on `auto` |
| A file failed with "parsing took longer than …" | Raise `RAG_PARSE_TIMEOUT_S`; very long scanned PDFs on CPU can take minutes with the layout reader |
| Port already in use | Change `RAG_UI_PORT` / `RAG_API_PORT` / `RAG_PORT` in `.env` |
| Validate warns "embedded Qdrant searches by brute force" | Expected in embedded mode; use `rag up` (server mode) for large corpora |
