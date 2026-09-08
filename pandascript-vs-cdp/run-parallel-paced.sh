#!/usr/bin/env bash
# pandascript-paced on Hacker News at 4/8/16, with plain pandascript as the
# same-run control (interleaved), into *-paced result dirs; the figure takes
# only the paced row from them (plot.load_parallel).
set -u
cd "$(dirname "$0")"
export LPD_PATH="$PWD/.bench-bin/lightpanda-14d868486"
export LPD_CACHE=1
LOG="$PWD/results/par-campaign.log"
phase() {
  local name="$1"; shift
  echo "=== [$(date +%H:%M:%S)] $name ===" | tee -a "$LOG"
  if uv run python "$@" >>"$LOG" 2>&1; then echo "--- [$(date +%H:%M:%S)] $name OK" | tee -a "$LOG"
  else echo "--- [$(date +%H:%M:%S)] $name FAILED (exit $?)" | tee -a "$LOG"; fi
}
echo "campaign par paced starting $(date -Is)" | tee -a "$LOG"
for n in 4 8 16; do
  phase "scrape cold x$n process paced" harness/bench.py --task scrape --mode cold --parallel $n --topology process \
    --configs pandascript,pandascript-paced --runs 8 --warmup 1 --pace 5 --out "results/par-scrape-cold-x${n}-process-paced"
done
echo "campaign par paced done $(date -Is)" | tee -a "$LOG"
