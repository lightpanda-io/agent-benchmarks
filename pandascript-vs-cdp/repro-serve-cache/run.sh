#!/usr/bin/env bash
# Offline repro for the serve-path HTTP cache regression.
# Runs the same 6-page walk (3 shared cacheable scripts per page, 30 ms
# server delay each) through four cells:
#   agent nocache | agent cache | serve nocache | serve cache
# Expected: cache makes agent ~3x FASTER and serve ~5-6x SLOWER.
set -euo pipefail
cd "$(dirname "$0")"

LIGHTPANDA="${LIGHTPANDA:-lightpanda}"
TRIALS="${TRIALS:-3}"
PORT=9300

cleanup() {
  [[ -n "${FIXTURE_PID:-}" ]] && kill "$FIXTURE_PID" 2>/dev/null || true
  [[ -n "${SERVE_PID:-}" ]] && kill "$SERVE_PID" 2>/dev/null || true
  [[ -n "${TMP:-}" ]] && rm -rf "$TMP"
}
trap cleanup EXIT

python3 ../harness/load_semantics_fixture.py "$PORT" >/dev/null &
FIXTURE_PID=$!
sleep 0.5

TMP="$(mktemp -d)"

walk_agent() { # $@ = extra lightpanda flags
  "$LIGHTPANDA" agent "$@" walk_agent.js 2>/dev/null | grep -o 'walk_ms=[0-9]*' | cut -d= -f2
}

walk_serve() { # $@ = extra lightpanda flags
  "$LIGHTPANDA" serve "$@" >/dev/null 2>&1 &
  SERVE_PID=$!
  sleep 0.5
  local ms
  ms="$(node walk_serve.js | grep -o 'walk_ms=[0-9]*' | cut -d= -f2)"
  kill "$SERVE_PID" 2>/dev/null || true
  wait "$SERVE_PID" 2>/dev/null || true
  SERVE_PID=
  echo "$ms"
}

cell() { # $1 = label, $2 = agent|serve, $@ = flags
  local label=$1 path=$2
  shift 2
  local out=()
  for _ in $(seq "$TRIALS"); do
    if [[ $path == agent ]]; then out+=("$(walk_agent "$@")"); else out+=("$(walk_serve "$@")"); fi
  done
  printf '%-14s %s ms\n' "$label" "${out[*]}"
}

echo "lightpanda: $("$LIGHTPANDA" version 2>/dev/null || echo unknown)"
echo "trials per cell: $TRIALS (cache dir persists across trials within a cell)"
echo
cell "agent nocache" agent
cell "agent cache" agent --http-cache-dir "$TMP/agent-cache"
cell "serve nocache" serve
cell "serve cache" serve --http-cache-dir "$TMP/serve-cache"
