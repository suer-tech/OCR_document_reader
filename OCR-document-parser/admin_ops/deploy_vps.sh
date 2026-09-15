#!/usr/bin/env bash
set -euo pipefail

target_sha="${1:?Exact release SHA is required}"
if [[ ! "$target_sha" =~ ^[a-f0-9]{40}$ ]]; then
  echo "Invalid release SHA" >&2
  exit 2
fi

deploy_dir=/opt/ocr-platform/OCR-document-parser
cd "$deploy_dir"
repo_root="$(git rev-parse --show-toplevel)"
if [[ "$(git rev-parse --show-prefix)" != "OCR-document-parser/" ]]; then
  echo "VPS OCR directory is not the OCR-document-parser subdirectory of the GitHub checkout" >&2
  exit 3
fi

if [[ "$(git -C "$repo_root" branch --show-current)" != "main" ]]; then
  echo "VPS checkout is not on main" >&2
  exit 3
fi
if [[ -n "$(git -C "$repo_root" status --porcelain)" ]]; then
  echo "VPS checkout has local changes; refusing to overwrite them" >&2
  exit 3
fi

previous_sha="$(git -C "$repo_root" rev-parse HEAD)"
git -C "$repo_root" fetch --no-tags origin main
if [[ "$(git -C "$repo_root" rev-parse refs/remotes/origin/main)" != "$target_sha" ]]; then
  echo "Remote main differs from approved release" >&2
  exit 4
fi
git -C "$repo_root" merge --ff-only "$target_sha"
if [[ "$(git -C "$repo_root" rev-parse HEAD)" != "$target_sha" ]]; then
  echo "VPS checkout did not reach approved release" >&2
  exit 4
fi

echo "Deploying ${target_sha:0:8}; previous revision ${previous_sha:0:8}"
docker compose -f docker-compose.yml up -d --build api worker admin-bot pulse-ai fixer-ai

for attempt in {1..12}; do
  if curl --fail --silent --show-error --max-time 3 http://127.0.0.1:8000/health >/dev/null && \
     docker compose -f docker-compose.yml exec -T worker python -c \
       'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8001/metrics", timeout=3).read(128)' >/dev/null && \
     docker compose -f docker-compose.yml exec -T admin-bot python -c \
       'import os; assert os.environ.get("OPS_TELEGRAM_TOKEN") and os.environ.get("OPS_GITHUB_TOKEN")' >/dev/null && \
     docker compose -f docker-compose.yml exec -T pulse-ai python -c \
       'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8080/openapi.json", timeout=3).read(128)' >/dev/null && \
     docker compose -f docker-compose.yml exec -T fixer-ai python -c \
       'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8080/openapi.json", timeout=3).read(128)' >/dev/null; then
    echo "OCR API, worker metrics and admin services are healthy at ${target_sha:0:8}"
    exit 0
  fi
  sleep 5
done

echo "Post-deployment smoke checks failed. Previous Git revision: $previous_sha. Manual recovery required." >&2
exit 5
