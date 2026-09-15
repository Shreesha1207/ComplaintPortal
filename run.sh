#!/usr/bin/env bash
# Start the server. Seeds the demo corpus on first run.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8000}"
python3 -c "import fastapi, uvicorn" 2>/dev/null || {
  echo "Installing dependencies…"; pip install -r requirements.txt; }
echo "→ http://127.0.0.1:${PORT}   (docs at /api/docs)"
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" "$@"
