# syntax=docker/dockerfile:1

FROM python:3.14-slim-trixie AS builder

WORKDIR /app

RUN python -m pip install --no-cache-dir "uv==0.12.0"

ENV UV_PROJECT_ENVIRONMENT="/opt/vidxp" \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

COPY pyproject.toml uv.lock README.md LICENSE MANIFEST.in ./
COPY src ./src

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync \
        --frozen \
        --extra local-worker \
        --extra frontend \
        --no-dev \
        --no-editable \
    && uv pip check --python /opt/vidxp/bin/python

FROM python:3.14-slim-trixie AS runtime

LABEL org.opencontainers.image.title="VidXP" \
    org.opencontainers.image.description="Local-first video indexing and search" \
    org.opencontainers.image.source="https://github.com/grayhatdevelopers/vidxp" \
    org.opencontainers.image.licenses="MIT"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system vidxp \
    && useradd --system --gid vidxp --home-dir /var/lib/vidxp vidxp \
    && mkdir -p /var/lib/vidxp \
    && chown vidxp:vidxp /var/lib/vidxp

COPY --from=builder /opt/vidxp /opt/vidxp

ENV PATH="/opt/vidxp/bin:${PATH}" \
    HOME="/var/lib/vidxp" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_HEADLESS=true \
    VIDXP_CONFIG_FILE="/var/lib/vidxp/config/repositories.json" \
    VIDXP_INDEX_DIR="/var/lib/vidxp/index"

USER vidxp
WORKDIR /var/lib/vidxp

VOLUME ["/var/lib/vidxp"]

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)"]

CMD ["vidxp", "ui", "--host", "0.0.0.0", "--port", "8501"]
