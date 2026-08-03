#!/usr/bin/env bash
set -euo pipefail

export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-ci-postgres-password}"
export VIDXP_ARTIFACT_DOWNLOAD_SECRET="${VIDXP_ARTIFACT_DOWNLOAD_SECRET:-ci-artifact-download-secret-000000}"
export VIDXP_CONTROL_IMAGE="${VIDXP_CONTROL_IMAGE:-vidxp-control:ci}"
export VIDXP_PUBLIC_API_HOST="${VIDXP_PUBLIC_API_HOST:-api.example.test}"
export VIDXP_UPLOAD_CLEANUP_TOKEN="${VIDXP_UPLOAD_CLEANUP_TOKEN:-ci-upload-cleanup-token-0000000000}"
export VIDXP_UPLOAD_CORS_ORIGIN_REGEX="${VIDXP_UPLOAD_CORS_ORIGIN_REGEX:-^(https://api\.example\.test)$}"
export VIDXP_UPLOAD_HANDOFF_PUBLIC_URL="${VIDXP_UPLOAD_HANDOFF_PUBLIC_URL:-https://api.example.test/upload-handoff}"
export VIDXP_UPLOAD_HANDOFF_SECRET="${VIDXP_UPLOAD_HANDOFF_SECRET:-ci-upload-handoff-secret-000000000}"
export VIDXP_UPLOAD_PUBLIC_ENDPOINT="${VIDXP_UPLOAD_PUBLIC_ENDPOINT:-https://uploads.example.test/uploads/}"
export VIDXP_WORKER_IMAGE="${VIDXP_WORKER_IMAGE:-vidxp-worker:ci}"

docker compose -f compose.yaml config --quiet
docker compose -f compose.coolify.yaml config --quiet
