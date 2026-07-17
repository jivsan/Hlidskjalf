#!/usr/bin/env bash
# Start Hlidskjalf for development. One command, no remembering.
#
#   ./scripts/dev.sh              # panel on :8787, serving the built SPA
#   ./scripts/dev.sh --reload     # + restart the backend on every save
#   ./scripts/dev.sh --vite       # + Vite on :5173 with hot reload (use that URL)
#   ./scripts/dev.sh --mock       # against dev/mock_pve.py, no real Proxmox needed
#
# This is a DEV launcher. Real deployments do not use it — Docker has its own
# entrypoint, the NixOS module has systemd, and a plain install runs uvicorn (or
# the `hlidskjalf` console script) under systemd. See docs/docker.md, nix/module.nix
# and docs/dev-against-real-proxmox.md §7.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

HOST="${HLIDSKJALF_DEV_HOST:-0.0.0.0}"
PORT="${HLIDSKJALF_DEV_PORT:-8787}"
ENV_FILE="${HLIDSKJALF_ENV_FILE:-dev/dev.env}"

RELOAD=0
VITE=0
MOCK=0
for arg in "$@"; do
  case "$arg" in
    --reload) RELOAD=1 ;;
    --vite)   VITE=1 ;;
    --mock)   MOCK=1 ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

die() { echo "error: $*" >&2; exit 1; }

[[ -x .venv/bin/uvicorn ]] || die "no .venv — run:
  python3 -m venv .venv && .venv/bin/pip install -e ./backend python-multipart"

# The SPA is served as static files by the backend, so it has to exist. Vite mode
# serves it itself and the backend must NOT also serve a stale dist/.
if [[ $VITE -eq 0 && ! -f frontend/dist/index.html ]]; then
  echo "== building the SPA (first run) =="
  [[ -d frontend/node_modules ]] || (cd frontend && npm ci)
  (cd frontend && npm run build)
fi
# ...and the backend has to be TOLD where it is — static_dir defaults to ""
# (API only). Without this every non-Vite run 404s the SPA it just built.
if [[ $VITE -eq 0 ]]; then
  export HLIDSKJALF_STATIC_DIR="${HLIDSKJALF_STATIC_DIR:-$ROOT/frontend/dist}"
fi

if [[ $MOCK -eq 1 ]]; then
  # The mock needs no secrets and no Proxmox. Everything is disposable.
  echo "== starting mock Proxmox on :18006 =="
  (cd dev && exec ../.venv/bin/uvicorn mock_pve:app --port 18006) &
  MOCK_PID=$!
  trap 'kill $MOCK_PID 2>/dev/null || true' EXIT
  sleep 1
  export HLIDSKJALF_PVE_HOST=127.0.0.1
  export HLIDSKJALF_PVE_PORT=18006
  export HLIDSKJALF_PVE_SCHEME=http     # http is for the mock ONLY
  export HLIDSKJALF_PVE_NODE=pve      # must match the mock's node name
  export HLIDSKJALF_PVE_TOKEN_ID='mock@pve!panel'
  export HLIDSKJALF_PVE_TOKEN_SECRET=mock-secret
  export HLIDSKJALF_COOKIE_SECURE=false
  export HLIDSKJALF_STATE_DIR="${HLIDSKJALF_STATE_DIR:-$ROOT/.dev-state}"
  mkdir -p "$HLIDSKJALF_STATE_DIR"
elif [[ -f "$ENV_FILE" ]]; then
  echo "== config: $ENV_FILE =="
  set -a; source "$ENV_FILE"; set +a
else
  die "no $ENV_FILE. Copy dev/dev.env.example to dev/dev.env and fill it in,
  or run against the mock instead:  ./scripts/dev.sh --mock"
fi

# Nothing is protected by default — an admin could destroy the VM the panel runs on.
if [[ -z "${HLIDSKJALF_PROTECTED_VMIDS:-}" && $MOCK -eq 0 ]]; then
  echo "WARNING: HLIDSKJALF_PROTECTED_VMIDS is empty — NOTHING is protected from"
  echo "         destroy, including the machine this panel runs on. Set it in $ENV_FILE."
fi

if [[ $VITE -eq 1 ]]; then
  export HLIDSKJALF_STATIC_DIR=""   # let Vite serve the SPA, not a stale dist/
  [[ -d frontend/node_modules ]] || (cd frontend && npm ci)
  echo "== Vite on :5173 (proxies /api and /ws to :$PORT) — open THAT url =="
  (cd frontend && exec npm run dev) &
  VITE_PID=$!
  trap 'kill ${VITE_PID:-} ${MOCK_PID:-} 2>/dev/null || true' EXIT
fi

ARGS=(--host "$HOST" --port "$PORT")
[[ $RELOAD -eq 1 ]] && ARGS+=(--reload)

echo "== panel on http://${HOST}:${PORT} =="
cd backend
exec ../.venv/bin/uvicorn hlidskjalf.main:app "${ARGS[@]}"
