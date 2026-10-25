#!/usr/bin/env bash
# Double-click launcher: Docker stack if Docker is running, otherwise a local run.
cd "$(dirname "$0")/.." || exit 1
if docker info >/dev/null 2>&1; then
  ./scripts/rag.sh up && open http://localhost:8080
else
  [[ -d backend/.venv ]] || ./scripts/rag.sh setup --yes
  (sleep 4 && open http://127.0.0.1:8000) &
  ./scripts/rag.sh run
fi
