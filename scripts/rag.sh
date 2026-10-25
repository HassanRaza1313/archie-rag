#!/usr/bin/env bash
# ragkit command line for macOS, Linux and Git Bash. PowerShell twin: scripts/rag.ps1.
# Usage: ./scripts/rag.sh <command> [options]    (./scripts/rag.sh help)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
COMPOSE=(docker compose -f "$ROOT/deploy/compose.yaml" --env-file "$ROOT/.env")

if [[ -x "$BACKEND/.venv/bin/python" ]]; then PYTHON="$BACKEND/.venv/bin/python"; else PYTHON="$BACKEND/.venv/Scripts/python.exe"; fi

info() { printf '\033[1;34m›\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
prefix() { while IFS= read -r line; do printf '%s %s
' "$1" "$line"; done; }
confirm() { read -r -p "$1 [y/N] " reply; [[ "$reply" =~ ^[Yy]$ ]]; }

ensure_env() {
  if [[ ! -f "$ROOT/.env" ]]; then
    cp "$ROOT/.env.example" "$ROOT/.env"
    info "created .env from .env.example"
  fi
}

ragkit() {
  [[ -x "$PYTHON" ]] || fail "backend not installed; run: ./scripts/rag.sh setup"
  (cd "$ROOT" && "$PYTHON" -m ragkit.cli "$@")
}

cmd_setup() {
  local yes=false
  [[ "${1:-}" == "--yes" ]] && yes=true
  ensure_env
  info "installing the backend"
  if have uv; then
    (cd "$BACKEND" && uv sync --frozen --extra rerank --extra dev)
  else
    have python3 || have python || fail "Python 3.10+ is required (or install uv: https://docs.astral.sh/uv/)"
    local py; py="$(command -v python3 || command -v python)"
    [[ -d "$BACKEND/.venv" ]] || "$py" -m venv "$BACKEND/.venv"
    if [[ -x "$BACKEND/.venv/bin/python" ]]; then PYTHON="$BACKEND/.venv/bin/python"; else PYTHON="$BACKEND/.venv/Scripts/python.exe"; fi
    "$PYTHON" -m pip install --quiet --upgrade pip
    "$PYTHON" -m pip install --quiet -e "${BACKEND}[rerank,dev]"
  fi
  if have npm; then
    info "installing the frontend"
    (cd "$FRONTEND" && npm ci --no-audit --no-fund)
  else
    info "Node.js not found: skipping the frontend (needed only for 'dev' and 'run'; Docker builds it for you)"
  fi
  if have ollama && { $yes || confirm "Pull the default models into Ollama now?"; }; then cmd_models pull; fi
  info "done. Next: ./scripts/rag.sh dev   (or ./scripts/rag.sh up for Docker)"
}

cmd_doctor() {
  if [[ -x "$PYTHON" ]]; then ragkit doctor || true; else info "backend not installed yet (run setup)"; fi
  if have docker && docker compose version >/dev/null 2>&1; then info "docker compose: $(docker compose version --short)"
  else info "docker compose: not found (needed only for 'up')"; fi
  if have node; then info "node: $(node --version)"; else info "node: not found (needed only for 'dev'/'run')"; fi
}

cmd_dev() {
  ensure_env
  [[ -d "$FRONTEND/node_modules" ]] || fail "frontend not installed; run: ./scripts/rag.sh setup"
  info "API on http://127.0.0.1:8000 (auto-reload) · UI on http://localhost:5173"
  trap 'kill 0' EXIT INT TERM
  ragkit serve --with-worker --reload 2>&1 | prefix "[api]" &
  (cd "$FRONTEND" && npm run dev -- --strictPort) 2>&1 | prefix "[ui] " &
  wait
}

cmd_run() {
  ensure_env
  [[ -d "$FRONTEND/node_modules" ]] || fail "frontend not installed; run: ./scripts/rag.sh setup"
  info "building the UI"
  (cd "$FRONTEND" && npm run build >/dev/null)
  info "open http://127.0.0.1:${RAG_PORT:-8000}"
  RAG_UI_DIST_DIR="$FRONTEND/dist" ragkit serve --with-worker
}

compose_files() {
  local files=() ollama=false
  for arg in "$@"; do
    case "$arg" in
      --gpu) files+=(-f "$ROOT/deploy/compose.gpu.yaml"); ollama=true ;;
      --with-ollama) ollama=true ;;
      --release) files+=(-f "$ROOT/deploy/compose.release.yaml") ;;
      --observability) files+=(--profile observability) ;;
    esac
  done
  $ollama && files+=(--profile ollama)
  printf '%s\n' "${files[@]+"${files[@]}"}"
}

cmd_up() {
  ensure_env
  have docker || fail "Docker is required for 'up' (or use: ./scripts/rag.sh run)"
  local extra=() build=() arg line
  while IFS= read -r line; do [[ -n "$line" ]] && extra+=("$line"); done < <(compose_files "$@")
  for arg in "$@"; do
    case "$arg" in
      --build) build=(--build) ;;
      --with-ollama|--gpu) export RAG_OLLAMA_URL="http://ollama:11434" ;;
      --release|--observability) ;;
      *) fail "unknown option for up: $arg" ;;
    esac
  done
  info "starting containers (first run builds images; this takes a few minutes)"
  "${COMPOSE[@]}" "${extra[@]+"${extra[@]}"}" up -d --wait "${build[@]+"${build[@]}"}"
  info "UI:  http://localhost:${RAG_UI_PORT:-8080}"
  info "API: http://localhost:${RAG_API_PORT:-8000}/docs"
  [[ " $* " == *" --observability "* ]] && info "Prometheus: http://localhost:${RAG_PROMETHEUS_PORT:-9090}"
  return 0
}

cmd_down() {
  if [[ "${1:-}" == "--volumes" ]]; then
    confirm "Delete ALL data volumes (vectors, chatbots, traces, models)?" || exit 0
    "${COMPOSE[@]}" --profile ollama --profile observability down --volumes
  else
    "${COMPOSE[@]}" --profile ollama --profile observability down
  fi
}

cmd_build() {
  local tag="${RAG_VERSION:-local}" push=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tag) tag="$2"; shift ;;
      --push) push=true ;;
      *) fail "unknown option for build: $1" ;;
    esac
    shift
  done
  local prefix="${RAG_IMAGE_PREFIX:-ragkit}"
  for target in api ui aio; do
    info "building $prefix-$target:$tag"
    if $push; then
      docker buildx build --platform linux/amd64,linux/arm64 -f "$ROOT/deploy/docker/Dockerfile" \
        --target "$target" -t "$prefix-$target:$tag" --push "$ROOT"
    else
      docker build -f "$ROOT/deploy/docker/Dockerfile" --target "$target" -t "$prefix-$target:$tag" "$ROOT"
    fi
  done
}

cmd_models() {
  [[ "${1:-}" == "pull" ]] || fail "usage: rag models pull"
  local models="${RAG_PULL_MODELS:-nomic-embed-text qwen3.5:9b}" model
  if have ollama; then
    for model in $models; do info "ollama pull $model"; ollama pull "$model"; done
  else
    "${COMPOSE[@]}" --profile ollama run --rm ollama-init
  fi
}

passthrough() {
  if [[ -x "$PYTHON" ]]; then ragkit "$@"
  else "${COMPOSE[@]}" exec api ragkit "$@"; fi
}

cmd_reset() {
  confirm "Delete local state in .ragkit (chatbots, embedded vectors, traces)?" || exit 0
  rm -rf "$ROOT/.ragkit"
  info "local state removed"
}

usage() {
  cat <<'EOF'
ragkit — build agentic RAG chatbots over your own documents

  setup [--yes]                 install dependencies, create .env, optionally pull models
  doctor                        check that everything needed is reachable
  dev                           hot-reload API + UI for development (http://localhost:5173)
  run                           production-style local run without Docker (http://127.0.0.1:8000)
  up [--with-ollama] [--gpu] [--release] [--build] [--observability]
                                start the Docker stack (http://localhost:8080)
  down [--volumes]              stop the Docker stack (--volumes deletes data, asks first)
  build [--tag X] [--push]      build the api, ui and all-in-one images
  logs [service]                follow container logs
  status                        container status
  models pull                   pull the configured models into Ollama
  kb list|build|export ...      manage chatbots headlessly (see: rag kb --help)
  eval NAME GOLDEN.jsonl        evaluate retrieval on a golden set
  reset                         delete local state (asks first)
EOF
}

main() {
  local command="${1:-help}"
  shift || true
  # shellcheck source=/dev/null  # .env is user-created at runtime
  [[ -f "$ROOT/.env" ]] && set -a && . "$ROOT/.env" && set +a
  case "$command" in
    setup) cmd_setup "$@" ;;
    doctor) cmd_doctor ;;
    dev) cmd_dev ;;
    run) cmd_run ;;
    up) cmd_up "$@" ;;
    down) cmd_down "$@" ;;
    build) cmd_build "$@" ;;
    logs) "${COMPOSE[@]}" logs -f "$@" ;;
    status) "${COMPOSE[@]}" ps ;;
    models) cmd_models "$@" ;;
    kb|eval) passthrough "$command" "$@" ;;
    reset) cmd_reset ;;
    help|-h|--help) usage ;;
    *) usage; exit 2 ;;
  esac
}

main "$@"
