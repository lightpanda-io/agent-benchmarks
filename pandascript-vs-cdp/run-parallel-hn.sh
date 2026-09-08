#!/usr/bin/env bash
# Follow-up to run-parallel.sh, same binary and configs: Hacker News at 8 and
# 16 concurrent flows (the lightest of the live sites, the only one we push
# past N=4), then the fixture memory phases again with the 25 ms sampler
# (the first pass sampled at 150 ms, which under-samples PandaScript's
# 30-90 ms flows; the 150 ms results are kept under *.150ms).
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

echo "campaign $PREFIX follow-up (HN x8/x16, memory resample) starting $(date -Is)" | tee -a "$LOG"

for n in 8 16; do
  for mode in cold warm; do
    phase "scrape $mode x$n process" harness/bench.py --task scrape --mode $mode --parallel $n --topology process \
      --configs "$CONFIGS" --runs 8 --warmup 1 --pace 5 --out "results/${PREFIX}-scrape-${mode}-x${n}-process"
  done
  phase "memory scrape x$n process" harness/memprobe.py --tasks scrape --parallel $n --topology process \
    --configs "$CONFIGS" --iters 3 --pace 5 --out "results/${PREFIX}-memory-live-x${n}-process"
done

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
