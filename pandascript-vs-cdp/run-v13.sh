#!/usr/bin/env bash
# v13: the retail task moved from eu.gymshark.com (infinite scroll; a
# layout-less browser reports its sentinel visible on every observe, so
# lightpanda paginated to page 17 and loaded 4x the products Chrome did) to
# outdoorvoices.com (12 server-rendered cards on both engines). Same binary
# as v11 (main 14d868486, ReleaseFast), same seven configs; only the retail
# phases are re-run, the other tasks' v11 rows stand.
set -u
cd "$(dirname "$0")"

export LPD_PATH="$PWD/.bench-bin/lightpanda-14d868486"
export LPD_CACHE=1
export BROWSER_USE_BIN="${BROWSER_USE_BIN:-$HOME/.local/share/uv/tools/browser-use/bin/browser-use}"

PREFIX=v13
CONFIGS=pandascript,puppeteer-lightpanda,puppeteer-chrome,playwright-lightpanda,playwright-chrome,browseruse-chrome,browseruse-lightpanda
LOG="$PWD/results/${PREFIX}-campaign.log"
mkdir -p results

phase() {
  local name="$1"; shift
  echo "=== [$(date +%H:%M:%S)] $name ===" | tee -a "$LOG"
  if uv run python "$@" >>"$LOG" 2>&1; then
    echo "--- [$(date +%H:%M:%S)] $name OK" | tee -a "$LOG"
  else
    echo "--- [$(date +%H:%M:%S)] $name FAILED (exit $?)" | tee -a "$LOG"
  fi
}

echo "campaign $PREFIX starting $(date -Is), binary $(sha256sum "$LPD_PATH" | cut -c1-16)" | tee -a "$LOG"
phase "retail cold" harness/bench.py --task retail --mode cold --configs "$CONFIGS" --runs 12 --warmup 2 --pace 3 --out "results/${PREFIX}-retail-cold"
phase "retail warm" harness/bench.py --task retail --mode warm --configs "$CONFIGS" --runs 12 --warmup 2 --pace 3 --out "results/${PREFIX}-retail-warm"
phase "memory retail" harness/memprobe.py --configs "$CONFIGS" --tasks retail --iters 5 --pace 5 --out "results/${PREFIX}-memory"
echo "campaign $PREFIX done $(date -Is)" | tee -a "$LOG"
