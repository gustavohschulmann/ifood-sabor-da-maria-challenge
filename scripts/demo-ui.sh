#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

UI_HOST="${DEMO_UI_HOST:-127.0.0.1}"
UI_PORT="${DEMO_UI_PORT:-8765}"
HERMES_HOST="${API_SERVER_HOST:-127.0.0.1}"
if [[ "$HERMES_HOST" == "0.0.0.0" || "$HERMES_HOST" == "::" ]]; then
  HERMES_HOST="127.0.0.1"
fi
HERMES_PORT="${API_SERVER_PORT:-8642}"
HERMES_URL="${HERMES_API_URL:-http://${HERMES_HOST}:${HERMES_PORT}}"
GATEWAY_LOG="${TMPDIR:-/tmp}/sabor-hermes-gateway.log"
STARTED_GATEWAY=0

export HERMES_MAX_TOKENS="${HERMES_MAX_TOKENS:-8192}"
export API_SERVER_ENABLED="${API_SERVER_ENABLED:-true}"

if [[ -z "${API_SERVER_KEY:-}" && -f "${HOME}/.hermes/.env" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      API_SERVER_KEY=*)
        export API_SERVER_KEY="${line#API_SERVER_KEY=}"
        API_SERVER_KEY="${API_SERVER_KEY%\"}"
        API_SERVER_KEY="${API_SERVER_KEY#\"}"
        API_SERVER_KEY="${API_SERVER_KEY%\'}"
        API_SERVER_KEY="${API_SERVER_KEY#\'}"
        ;;
    esac
  done < "${HOME}/.hermes/.env"
fi
# Hermes stores the live key in config.yaml when set via `hermes config set`.
# Do not invent a fallback key: that 401s against an already-running gateway.

hermes_up() {
  curl -sf "${HERMES_URL}/health" >/dev/null 2>&1
}

wait_for_hermes() {
  local tries=0
  while (( tries < 60 )); do
    if hermes_up; then
      return 0
    fi
    sleep 0.5
    tries=$((tries + 1))
  done
  return 1
}

pids_on_port() {
  lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

stop_port() {
  local port="$1"
  local pids
  pids="$(pids_on_port "$port")"
  if [[ -z "$pids" ]]; then
    return 0
  fi
  echo "Stopping listener on :${port} (${pids//$'\n'/ })"
  # shellcheck disable=SC2086
  kill $pids >/dev/null 2>&1 || true
  sleep 0.4
  pids="$(pids_on_port "$port")"
  if [[ -n "$pids" ]]; then
    # shellcheck disable=SC2086
    kill -9 $pids >/dev/null 2>&1 || true
    sleep 0.2
  fi
}

stop_mcp() {
  pkill -f -- "--directory ${ROOT} run python -m src.mcp.server" >/dev/null 2>&1 || true
}

cleanup() {
  if [[ "$STARTED_GATEWAY" -eq 1 && -n "${GATEWAY_PID:-}" ]]; then
    kill "$GATEWAY_PID" >/dev/null 2>&1 || true
    stop_mcp
  fi
}
trap cleanup EXIT

if ! command -v hermes >/dev/null 2>&1; then
  echo "hermes not found on PATH. Install Hermes Agent first." >&2
  exit 1
fi

echo "Restarting Hermes gateway so sabor_da_maria MCP loads current tools..."
stop_port "$HERMES_PORT"
stop_mcp
sleep 0.5
: >"$GATEWAY_LOG"
echo "Logs: ${GATEWAY_LOG}"
hermes gateway run --replace --force >>"$GATEWAY_LOG" 2>&1 &
GATEWAY_PID=$!
STARTED_GATEWAY=1
if ! wait_for_hermes; then
  echo "Hermes API did not come up at ${HERMES_URL}." >&2
  echo "Enable it with API_SERVER_ENABLED=true and API_SERVER_KEY in ~/.hermes/.env" >&2
  echo "Then inspect ${GATEWAY_LOG}" >&2
  exit 1
fi

stop_port "$UI_PORT"

echo "Demo UI: http://${UI_HOST}:${UI_PORT}"
exec uv run python -u scripts/demo_ui.py
