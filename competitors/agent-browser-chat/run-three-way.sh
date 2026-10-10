#!/usr/bin/env bash
# GAIA L1 and AssistantBench on three stacks, one model, back to back:
#   native     lightpanda agent, Keenable search (keyless)
#   ab-lightpanda / ab-chrome   agent-browser chat, Lightpanda or Chrome engine
# One task at a time, answer sources blocked everywhere, then leak-audit.
#
#   LP=/path/to/lightpanda AB=/path/to/agent-browser \
#     competitors/agent-browser-chat/run-three-way.sh
#
# Needs HF_TOKEN, GOOGLE_API_KEY and bwrap. Optional: MODEL (default
# gemini-3.8-flash), TS (output timestamp; reuse it with RESUME=--resume to
# continue a stopped run), NATIVE_SUITES / AB_SUITES to run a subset.
set -u
cd "$(dirname "$0")/../.."
LP=${LP:?path to a lightpanda build}
AB=${AB:?path to the patched agent-browser}
MODEL=${MODEL:-gemini-3.8-flash}
TS=${TS:-$(date -u +%Y%m%dT%H%MZ)}
COMMON=(--model "$MODEL" --workers 1 --timeout 1800 --block-answer-sources ${RESUME:-})
LOG=results/three-way-logs/$TS
mkdir -p "$LOG"
# With no search key set, the native agent searches with Keenable.
NOSEARCH=(-u BRAVE_API_KEY -u TAVILY_API_KEY -u EXA_API_KEY -u KEENABLE_API_KEY)

step() { echo "$(date -u +%H:%M) start $1"; }
for suite in ${NATIVE_SUITES-assistantbench gaia}; do
  step "$suite-native"
  env "${NOSEARCH[@]}" uv run "$suite-run" --provider gemini --lightpanda "$LP" "${COMMON[@]}" \
    --out-dir "results/$suite-three-way/native/$TS" > "$LOG/$suite-native.log" 2>&1
done
for suite in ${AB_SUITES-gaia assistantbench}; do
  for engine in lightpanda chrome; do
    step "$suite-ab-$engine"
    extra=(); [ "$engine" = lightpanda ] && extra=(--lightpanda "$LP")
    GEMINI_DIRECT=1 uv run "$suite-ab-run" --agent-browser "$AB" --engine "$engine" "${extra[@]}" \
      "${COMMON[@]}" --out-dir "results/$suite-three-way/ab-$engine/$TS" \
      > "$LOG/$suite-ab-$engine.log" 2>&1
  done
done
step leak-audit
uv run leak-audit results/*-three-way/*/"$TS"/predictions.jsonl | tee "$LOG/leak-audit.txt"
echo "$(date -u +%H:%M) done"
