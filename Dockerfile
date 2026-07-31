# syntax=docker/dockerfile:1

FROM python:3.14-slim-trixie AS build-base

WORKDIR /app

RUN python -m pip install --no-cache-dir "uv==0.12.0"

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

COPY pyproject.toml uv.lock README.md LICENSE MANIFEST.in ./
COPY src ./src

FROM build-base AS local-builder
ENV UV_PROJECT_ENVIRONMENT="/opt/vidxp"
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra local-worker --extra frontend \
        --no-dev --no-editable \
    && uv pip check --python /opt/vidxp/bin/python

FROM build-base AS control-builder
ENV UV_PROJECT_ENVIRONMENT="/opt/vidxp"
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra server --no-dev --no-editable \
    && uv pip check --python /opt/vidxp/bin/python

FROM build-base AS worker-builder
ENV UV_PROJECT_ENVIRONMENT="/opt/vidxp"
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra server-worker \
        --no-dev --no-editable \
    && uv pip check --python /opt/vidxp/bin/python

FROM python:3.14-slim-trixie AS runtime-base

LABEL org.opencontainers.image.title="VidXP" \
    org.opencontainers.image.description="Video indexing and search" \
    org.opencontainers.image.source="https://github.com/grayhatdevelopers/vidxp" \
    org.opencontainers.image.licenses="MIT"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 vidxp \
    && useradd --system --uid 1000 --gid vidxp \
        --home-dir /var/lib/vidxp vidxp \
    && mkdir -p /var/lib/vidxp \
    && chown vidxp:vidxp /var/lib/vidxp

ENV PATH="/opt/vidxp/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIDXP_HTTP_PORT=8000

USER vidxp
WORKDIR /var/lib/vidxp

FROM runtime-base AS control
COPY --from=control-builder /opt/vidxp /opt/vidxp
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"]
CMD ["vidxp-api"]

FROM runtime-base AS worker
COPY --from=worker-builder /opt/vidxp /opt/vidxp
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "-m", "vidxp.health_cli", "worker"]
CMD ["vidxp-worker", "--role", "cpu"]

FROM runtime-base AS local
COPY --from=local-builder /opt/vidxp /opt/vidxp
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_HEADLESS=true \
    VIDXP_CONFIG_FILE="/var/lib/vidxp/config/repositories.json" \
    VIDXP_INDEX_DIR="/var/lib/vidxp/index"
VOLUME ["/var/lib/vidxp"]
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)"]
CMD ["vidxp", "ui", "--host", "0.0.0.0", "--port", "8501"]
