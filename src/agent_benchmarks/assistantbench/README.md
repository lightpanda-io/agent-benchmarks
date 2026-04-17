# AssistantBench

Drives the `lightpanda agent` one-shot mode against the AssistantBench dataset
(214 live-web QA tasks) and scores the predictions with a port of the metric
from the AssistantBench paper.

## Why this benchmark

Lightpanda has no paint/layout pipeline, so screenshot-based graders
(WebVoyager, Online-Mind2Web's WebJudge, VisualWebArena) don't apply.
AssistantBench grades text answers against a gold string/number/list/dict, so
it works without rendered pixels.

## Prerequisites

```bash
# From the browser repo root
zig build -Doptimize=ReleaseFast

# Default provider is gemini — set the API key
export GOOGLE_API_KEY=...
```

## Usage

Run from the browser repo root so the default `zig-out/bin/lightpanda` path
resolves. Override with `--lightpanda` otherwise.

```bash
# Smoke test (3 dev tasks)
uv run assistantbench-run --limit 3

# Full dev split (33 tasks, sequential)
uv run assistantbench-run --split validation

# Parallel (4 concurrent lightpanda processes)
uv run assistantbench-run --split validation --workers 4

# Resume a partial run
uv run assistantbench-run --split validation --resume \
    --out-dir results/assistantbench/20260416T120000Z

# Score only (skip running)
uv run assistantbench-grade results/assistantbench/20260416T120000Z/predictions.jsonl
```

## Output layout

```
results/
  assistantbench/
    <UTC-timestamp>/
      predictions.jsonl   # {id, task, gold, prediction, duration_s, timed_out, stderr_tail, ...}
      scores.json         # aggregate + per-task scores
```

`scores.json` reports:

- `accuracy_soft` — mean per-task score in [0, 1]
- `accuracy_strict` — fraction of tasks scoring ≥ 0.5
- `by_difficulty` — breakdown on `Medium` / `Hard`
- `avg_duration_s`, `timeouts`

## Options

- `--split {validation,test}` — default `validation` (33 tasks); `test` has 181
- `--limit N` — cap the number of tasks
- `--workers N` — parallel subprocesses (default 1). Be gentle on live sites.
- `--timeout SECONDS` — per-task timeout (default 300)
- `--provider PROVIDER` — `gemini` / `anthropic` / `openai` / `ollama`
- `--model MODEL` — override provider default
- `--lightpanda PATH` — binary path, resolved against CWD (default `zig-out/bin/lightpanda`)
- `--resume` / `--out-dir DIR` — resume a prior run

## Grading — notes

The upstream AssistantBench grader had not been released at the time of
writing, so `grade.py` implements the metric described in section 4.1 of the
paper (arxiv 2407.15711):

- **Strings:** token-level F1 (lowercased, punctuation-stripped).
- **Numbers:** `max(0, 1 - |p - g| / max(|g|, 1))`.
- **Lists:** best-alignment F1, per-element scored by type.
- **Dicts:** mean of per-key score.
- **Empty prediction:** 0 (1 if gold is also empty).

The paper's published GPT-4 dev score is ~25% accuracy (strict). Use that as
a reference point when reading Lightpanda's numbers.
