#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git pull --ff-only
fi

bash scripts/prepare-bind-mounts.sh
if grep -Eq 'ChangeMe|replace-with' .env; then
  echo "Set the sample values in .env before updating the stack." >&2
  exit 1
fi
docker compose config --quiet
docker compose up -d --build --remove-orphans
docker compose -f compose.pki.yaml build pki
docker compose ps
