#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

bash scripts/prepare-bind-mounts.sh

if grep -Eq 'ChangeMe|replace-with' .env; then
  cat >&2 <<'EOF'
.env still contains example credentials. Set unique passwords and replace NETRIG_SECRET
with the output of `openssl rand -hex 32`, then run scripts/setup.sh again.
EOF
  exit 1
fi
if ! grep -q '^FTP_PASSWORD=.' .env; then
  echo "Set a non-empty FTP_PASSWORD in .env before starting FTP." >&2
  exit 1
fi

docker compose config --quiet
docker compose up -d --build --remove-orphans
docker compose ps
