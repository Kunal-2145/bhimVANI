#!/usr/bin/env bash
# ============================================================
# BimaVani (बीमा-वाणी) 1-click startup (macOS / Linux)
#   ./run.sh            -> create venv if needed, install deps, start server
#   ./run.sh --test     -> run the pytest suite instead
#   ./run.sh --verify   -> run live Gnani API smoke test (needs GNANI_API_KEY)
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

VENV_DIR=".venv"
PY="$VENV_DIR/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "[run.sh] Creating virtualenv..."
  python3 -m venv "$VENV_DIR"
fi

echo "[run.sh] Installing/upgrading dependencies..."
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r requirements.txt

mkdir -p backend/cache/tts
touch backend/data/call_history.json

MODE="${1:-}"
case "$MODE" in
  --test)
    "$VENV_DIR/bin/python" -m pytest -q tests/
    ;;
  --verify)
    "$VENV_DIR/bin/python" verify_gnani_api.py
    ;;
  *)
    if [[ -f .env ]]; then
      echo "[run.sh] Loading .env"
      set -a; source .env; set +a
    else
      echo "[run.sh] NOTE: no .env found -> Credit-Shield Fallback Mode (demo-safe)."
      echo "         Copy .env.example -> .env and add GNANI_API_KEY for live Gnani."
    fi
    echo "[run.sh] Starting BimaVani on http://127.0.0.1:8000  (Ctrl+C to stop)"
    exec "$VENV_DIR/bin/python" -m uvicorn backend.main:app \
      --host "${UVICORN_HOST:-127.0.0.1}" --port "${UVICORN_PORT:-8000}"
    ;;
esac
