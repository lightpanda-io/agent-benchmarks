#!/usr/bin/env bash
# v11 campaign: main 14d868486 (2026-09-05, ReleaseFast, stashed immutably as
# .bench-bin/lightpanda-14d868486) — the single-flow baseline for the same
# binary the `par` parallel campaign ran on, so one post can cite one build.
#
# Same ten phases and seven configs as v9/v10, shipped defaults, no lpd flags.
# Differences from v10: browser-use CLI 0.1.13 (was 0.1.9), the memory probe
# samples PSS every 25 ms (was 150 ms — that alone raises every peak, so the
# memory table is not comparable to v9's) and records CPU time, and the
# puppeteer scripts emit per-step timings.
set -u
cd "$(dirname "$0")"

export LPD_PATH="$PWD/.bench-bin/lightpanda-14d868486"
export LPD_CACHE=1
export BROWSER_USE_BIN="${BROWSER_USE_BIN:-$HOME/.local/share/uv/tools/browser-use/bin/browser-use}"

PREFIX=v11
# v9's exact seven. The harness also carries the Python legs and a
# pandascript-fresh A/B row, which belong to the Python campaign, not this one.
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

phase "scrape cold"   harness/bench.py --task scrape     --mode cold --configs "$CONFIGS" --runs 12 --warmup 2 --pace 3 --out "results/${PREFIX}-scrape-cold"
phase "scrape warm"   harness/bench.py --task scrape     --mode warm --configs "$CONFIGS" --runs 12 --warmup 2 --pace 3 --out "results/${PREFIX}-scrape-warm"
phase "scrape_par"    harness/bench.py --task scrape_par --mode cold --configs pandascript --runs 12 --warmup 2 --pace 3 --out "results/${PREFIX}-scrape_par-cold"
phase "retail cold"   harness/bench.py --task retail     --mode cold --configs "$CONFIGS" --runs 12 --warmup 2 --pace 3 --out "results/${PREFIX}-retail-cold"
phase "retail warm"   harness/bench.py --task retail     --mode warm --configs "$CONFIGS" --runs 12 --warmup 2 --pace 3 --out "results/${PREFIX}-retail-warm"
phase "news cold"     harness/bench.py --task news       --mode cold --configs "$CONFIGS" --runs 12 --warmup 2 --pace 3 --out "results/${PREFIX}-news-cold"
phase "news warm"     harness/bench.py --task news       --mode warm --configs "$CONFIGS" --runs 12 --warmup 2 --pace 3 --out "results/${PREFIX}-news-warm"
phase "login_fx cold" harness/bench.py --task login_fx   --mode cold --configs "$CONFIGS" --runs 20 --warmup 2 --pace 1 --out "results/${PREFIX}-login_fx-cold"
phase "login_fx warm" harness/bench.py --task login_fx   --mode warm --configs "$CONFIGS" --runs 20 --warmup 2 --pace 1 --out "results/${PREFIX}-login_fx-warm"
phase "memory"        harness/memprobe.py --configs "$CONFIGS" --tasks scrape,retail,news,login_fx --iters 5 --pace 5 --out "results/${PREFIX}-memory"

echo "campaign $PREFIX done $(date -Is)" | tee -a "$LOG"
