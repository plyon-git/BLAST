#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi
exec .venv/bin/uvicorn blastio.app:app --host 127.0.0.1 --port 8000 --workers 1
