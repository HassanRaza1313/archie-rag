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
(`RAG_INSTALL_OCR=true` at build time).

## 5. Configuration files

| File | Holds | Committed |
|---|---|---|
| `.env` | Infrastructure: URLs, ports, Qdrant mode, data roots, API key, logging | No (`.env.example` is) |
| Chatbot configs | Created in the UI or with `rag kb build`, versioned in SQLite, exported with `rag kb export` | Optional |
| `data/eval/*.jsonl` | Golden sets: `{"question": …, "expected_sources": ["file.md"]}` (empty list = should abstain) | Yes |

## 6. Security defaults

- Services bind to 127.0.0.1 and Docker publishes on 127.0.0.1. To expose them, set `RAG_PUBLISH_HOST`
  **and** `RAG_API_KEY`. A local non-loopback bind without a key is refused.
- Source folders must be inside `RAG_DATA_ROOTS`.
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
| Port already in use | Change `RAG_UI_PORT` / `RAG_API_PORT` / `RAG_PORT` in `.env` |
| Validate warns "embedded Qdrant searches by brute force" | Expected in embedded mode; use `rag up` (server mode) for large corpora |
