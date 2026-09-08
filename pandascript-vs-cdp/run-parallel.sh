#!/usr/bin/env bash
# Parallel-batch campaign on main 14d868486 (ReleaseFast, stashed immutably as
# .bench-bin/lightpanda-14d868486): the throughput regime that single-flow
# interleaving cannot show. Same seven configs as v9/v10.
#
# Fixture (login_fx, zero network): batch size 1/4/8/16, cold and warm, one
# engine per flow (process) and all flows on one engine (shared); the memory
# probe at the same sizes. Then the live sites at N=4 only (rate-limit risk).
# browseruse-chrome is skipped by the harness under shared (one tab per Chrome).
set -u
cd "$(dirname "$0")"

export LPD_PATH="$PWD/.bench-bin/lightpanda-14d868486"
export LPD_CACHE=1
export BROWSER_USE_BIN="${BROWSER_USE_BIN:-$HOME/.local/share/uv/tools/browser-use/bin/browser-use}"

PREFIX=par
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

echo "campaign $PREFIX starting $(date -Is), binary $(sha256sum "$LPD_PATH" | cut -c1-16), browser-use $("$BROWSER_USE_BIN" --version 2>&1 | head -1)" | tee -a "$LOG"

for n in 1 4 8 16; do
  for mode in cold warm; do
    phase "login_fx $mode x$n process" harness/bench.py --task login_fx --mode $mode --parallel $n --topology process \
      --configs "$CONFIGS" --runs 10 --warmup 2 --pace 1 --out "results/${PREFIX}-login_fx-${mode}-x${n}-process"
  done
done
for n in 4 8 16; do
  for mode in cold warm; do
    phase "login_fx $mode x$n shared" harness/bench.py --task login_fx --mode $mode --parallel $n --topology shared \
      --configs "$CONFIGS" --runs 10 --warmup 2 --pace 1 --out "results/${PREFIX}-login_fx-${mode}-x${n}-shared"
  done
done
for n in 1 4 8 16; do
  phase "memory login_fx x$n process" harness/memprobe.py --tasks login_fx --parallel $n --topology process \
    --configs "$CONFIGS" --iters 5 --pace 2 --out "results/${PREFIX}-memory-x${n}-process"
done
for n in 4 8 16; do
  phase "memory login_fx x$n shared" harness/memprobe.py --tasks login_fx --parallel $n --topology shared \
    --configs "$CONFIGS" --iters 5 --pace 2 --out "results/${PREFIX}-memory-x${n}-shared"
done

# Live sites, N=4, one engine per flow: sanity check that the fixture ratios
# transfer, not the main result. Longer pacing to stay under rate limits.
for task in scrape retail news; do
  for mode in cold warm; do
    phase "$task $mode x4 process" harness/bench.py --task $task --mode $mode --parallel 4 --topology process \
      --configs "$CONFIGS" --runs 8 --warmup 1 --pace 5 --out "results/${PREFIX}-${task}-${mode}-x4-process"
  done
done
phase "memory live x4 process" harness/memprobe.py --tasks scrape,retail,news --parallel 4 --topology process \
  --configs "$CONFIGS" --iters 3 --pace 5 --out "results/${PREFIX}-memory-live-x4-process"

echo "campaign $PREFIX done $(date -Is)" | tee -a "$LOG"
