#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 1 && "$#" -ne 3 ]]; then
  echo "usage: $0 PRODUCT_IMAGE [CONTROL_IMAGE WORKER_IMAGE]" >&2
  exit 2
fi

product="$1"
control="${2:-}"
worker="${3:-}"
container="vidxp-smoke-${GITHUB_RUN_ID:-local}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cleanup() {
  docker rm --force "$container" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run --detach --name "$container" \
  --publish 127.0.0.1:8501:8501 "$product"
docker exec "$container" vidxp --version
docker exec "$container" vidxp init --json
docker exec -i "$container" python - cpu < "$root/utils/verify_runtime.py"
docker exec "$container" sh -c \
  'test ! -d "$HOME/.cache/huggingface" && test ! -d "$HOME/.cache/clip"'

for _ in {1..30}; do
  if curl --fail --silent http://127.0.0.1:8501/_stcore/health; then
    break
  fi
  if [[ "$(docker inspect --format='{{.State.Running}}' "$container")" != "true" ]]; then
    docker logs "$container"
    exit 1
  fi
  sleep 2
done
curl --fail --silent http://127.0.0.1:8501/_stcore/health

if [[ -n "$control" ]]; then
  docker run --rm "$control" vidxp-api --help
  docker run --rm "$control" vidxp-mcp --help
  docker run --rm "$worker" vidxp init --json
fi
