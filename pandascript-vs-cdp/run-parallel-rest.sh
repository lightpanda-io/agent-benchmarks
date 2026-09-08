#!/usr/bin/env bash
# Continuation of run-parallel.sh after the retail N=4 phase was stopped
# (gymshark throttled the IP under 28 concurrent flows per rotation: zero
# product cards on every engine, PandaScript batches 3.5x slower). Retail is
# dropped from the parallel live pass; news runs as planned, then the live
# memory probe for scrape and news, then the done line the HN follow-up
# (run-parallel-hn.sh) waits for.
set -u
cd "$(dirname "$0")"

export LPD_PATH="$PWD/.bench-bin/lightpanda-14d868486"
export LPD_CACHE=1
export BROWSER_USE_BIN="${BROWSER_USE_BIN:-$HOME/.local/share/uv/tools/browser-use/bin/browser-use}"

PREFIX=par
CONFIGS=pandascript,puppeteer-lightpanda,puppeteer-chrome,playwright-lightpanda,playwright-chrome,browseruse-chrome,browseruse-lightpanda
LOG="$PWD/results/${PREFIX}-campaign.log"

phase() {
  local name="$1"; shift
  echo "=== [$(date +%H:%M:%S)] $name ===" | tee -a "$LOG"
  if uv run python "$@" >>"$LOG" 2>&1; then
    echo "--- [$(date +%H:%M:%S)] $name OK" | tee -a "$LOG"
  else
    echo "--- [$(date +%H:%M:%S)] $name FAILED (exit $?)" | tee -a "$LOG"
  fi
}

echo "campaign $PREFIX resuming without retail $(date -Is)" | tee -a "$LOG"
for mode in cold warm; do
  phase "news $mode x4 process" harness/bench.py --task news --mode $mode --parallel 4 --topology process \
    --configs "$CONFIGS" --runs 8 --warmup 1 --pace 5 --out "results/${PREFIX}-news-${mode}-x4-process"
done
phase "memory live x4 process" harness/memprobe.py --tasks scrape,news --parallel 4 --topology process \
  --configs "$CONFIGS" --iters 3 --pace 5 --out "results/${PREFIX}-memory-live-x4-process"

echo "campaign $PREFIX done $(date -Is)" | tee -a "$LOG"
