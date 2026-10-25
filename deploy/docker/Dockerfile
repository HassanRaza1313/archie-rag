# syntax=docker/dockerfile:1.7
# One Dockerfile, three targets:
#   api  - FastAPI app; the same image runs the build worker (`ragkit worker`)
#   ui   - built SPA on unprivileged nginx, proxying /api to the API
#   aio  - all-in-one: API + worker + UI + embedded Qdrant in a single container
# Build from the repository root:  docker build -f deploy/docker/Dockerfile --target api .

ARG PYTHON_IMAGE=python:3.12.15-slim
ARG NODE_IMAGE=node:22.23.4-alpine
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.31.7-alpine

FROM ghcr.io/astral-sh/uv:0.13.5 AS uv

# ---- Python dependencies (cached separately from source) ----
FROM ${PYTHON_IMAGE} AS python-build
ARG INSTALL_OCR=false
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    extras="--extra rerank"; [ "$INSTALL_OCR" = "true" ] && extras="$extras --extra ocr"; \
    uv sync --frozen --no-dev $extras --no-install-project
COPY backend/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    extras="--extra rerank"; [ "$INSTALL_OCR" = "true" ] && extras="$extras --extra ocr"; \
    uv sync --frozen --no-dev $extras --no-editable

# ---- UI build ----
FROM ${NODE_IMAGE} AS ui-build
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Python runtime base ----
FROM ${PYTHON_IMAGE} AS python-runtime
ARG INSTALL_OCR=false
RUN if [ "$INSTALL_OCR" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*; \
    fi \
    && useradd --create-home --uid 10001 ragkit \
    && mkdir -p /data/state /data/sources /data/models \
    && chown -R ragkit:ragkit /data
COPY --from=python-build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    RAG_STATE_DIR=/data/state \
    RAG_DATA_ROOTS=/data/sources \
    RAG_MODEL_CACHE_DIR=/data/models \
    RAG_BIND_HOST=0.0.0.0 \
    RAG_PUBLIC_BIND_OK=true \
    RAG_PORT=8000
USER ragkit
WORKDIR /home/ragkit
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/system/health', timeout=2)"]

# ---- Targets ----
FROM python-runtime AS api
ENV RAG_QDRANT_MODE=server RAG_QDRANT_URL=http://qdrant:6333
CMD ["ragkit", "serve"]

FROM python-runtime AS aio
COPY --from=ui-build --chown=ragkit:ragkit /ui/dist /opt/ui
ENV RAG_QDRANT_MODE=embedded RAG_UI_DIST_DIR=/opt/ui
VOLUME ["/data/state", "/data/models"]
CMD ["ragkit", "serve", "--with-worker"]

FROM ${NGINX_IMAGE} AS ui
COPY deploy/docker/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=ui-build /ui/dist /usr/share/nginx/html
ENV RAG_API_UPSTREAM=http://api:8000
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
