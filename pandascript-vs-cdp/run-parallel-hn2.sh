#!/usr/bin/env bash
# Second continuation: the remainder of run-parallel-hn.sh after the OOM kill
# in "scrape warm x16" (warm mode holds N browsers per config for all seven
# configs at once — ~100 browser processes with Hacker News pages resident;
# that phase is dropped, cold x16 and warm x8 cover HN). memprobe holds one
# config's batch at a time, so the memory phases are fine.
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

echo "campaign $PREFIX follow-up resuming after OOM (scrape warm x16 dropped) $(date -Is)" | tee -a "$LOG"

phase "memory scrape x16 process" harness/memprobe.py --tasks scrape --parallel 16 --topology process \
  --configs "$CONFIGS" --iters 3 --pace 5 --out "results/${PREFIX}-memory-live-x16-process"

for d in results/${PREFIX}-memory-x*-process results/${PREFIX}-memory-x*-shared; do
  [ -d "$d" ] && [ ! -d "$d.150ms" ] && mv "$d" "$d.150ms"
done
for n in 1 4 8 16; do
  phase "memory login_fx x$n process (25ms)" harness/memprobe.py --tasks login_fx --parallel $n --topology process \
    --configs "$CONFIGS" --iters 5 --pace 2 --out "results/${PREFIX}-memory-x${n}-process"
done
for n in 4 8 16; do
  phase "memory login_fx x$n shared (25ms)" harness/memprobe.py --tasks login_fx --parallel $n --topology shared \
    --configs "$CONFIGS" --iters 5 --pace 2 --out "results/${PREFIX}-memory-x${n}-shared"
done

echo "campaign $PREFIX follow-up done $(date -Is)" | tee -a "$LOG"
