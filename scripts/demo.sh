#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

uv run python scripts/reset_workspace.py

# Keep enough output room for sourced recipe and product-page extraction.
export HERMES_MAX_TOKENS="${HERMES_MAX_TOKENS:-8192}"

exec hermes chat --in "$ROOT" --query-file "$ROOT/scripts/demo-prompt.txt" \
  -t web,sabor_da_maria
