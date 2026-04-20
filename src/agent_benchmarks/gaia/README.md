# GAIA

Drives the `lightpanda agent` one-shot mode against the [GAIA](https://arxiv.org/abs/2311.12983)
benchmark and grades predictions with a port of the paper's
exact-match rubric.

## Scope

GAIA has 3 levels (1 = shortest tool-use chains, 3 = longest) and many
tasks include attached files (PDF, image, audio). `gaia-run` defaults
to **Level 1, all tasks** (53 rows in the `validation` split) so the
score is apples-to-apples with the paper's published Level-1 baseline.

Lightpanda has no PDF/audio/image reader, so tasks with attachments
(11/53 on Level 1) will score 0 — same as any other text-only browser.
To isolate agent performance on tasks the stack can *actually* solve,
pass `--skip-attachments` (runs only the 42 text-only rows). Pass
`--level 2` or `--level 3` for harder levels.

## Prerequisites

```bash
# From the browser repo root
zig build -Doptimize=ReleaseFast

# Default provider is gemini — set the API key
export GOOGLE_API_KEY=...

# GAIA is a gated HF dataset. Accept terms once at
# https://huggingface.co/datasets/gaia-benchmark/GAIA
# then set your token:
export HF_TOKEN=hf_...
```

> **License note:** The GAIA dataset is distributed under terms that
> forbid redistribution. Per the HF dataset card: *"you agree to not
> reshare this dataset outside of a gated or private repository."* The
> `predictions.jsonl` contains verbatim questions and gold answers, so
> `results/` MUST stay gitignored (it is, via `benchmarks/.gitignore`).
> Do not commit or publish anything under `results/gaia/`.

## Usage

Run from the browser repo root so the default `zig-out/bin/lightpanda`
resolves. Override with `--lightpanda` otherwise.

```bash
# Smoke test (3 dev tasks)
uv run gaia-run --limit 3

# Full filtered Level-1 dev split (~30–50 tasks)
uv run gaia-run --workers 8

# Try Level 2 (keeps attachment filter — most Level-2 rows have files)
uv run gaia-run --level 2 --workers 8

# Score only (skip running)
uv run gaia-grade results/gaia/20260417T120000Z/predictions.jsonl
```

## Output layout

```
results/
  gaia/
    <UTC-timestamp>/
      predictions.jsonl   # {id, task, gold, prediction, level, duration_s, ...}
      scores.json         # aggregate + per-task scores
```

`scores.json` reports:

- `accuracy` — fraction of tasks with score 1.0 (GAIA strict rubric)
- `accuracy_soft` — as above but numeric answers within 1% get half credit
- `by_level` — breakdown when multiple levels are mixed
- `avg_duration_s`, `timeouts`

## Options

- `--level {1,2,3}` — default 1
- `--split {validation,test}` — default `validation`; `test` answers are private
- `--skip-attachments` — exclude tasks with attached files (default: include, they score 0 to match the paper's baseline scope)
- `--limit N` — cap the number of tasks
- `--workers N` — parallel subprocesses (default 1). Be gentle on live sites.
- `--timeout SECONDS` — per-task timeout (default 300)
- `--provider PROVIDER` — `gemini` / `anthropic` / `openai` / `ollama`
- `--model MODEL` — override provider default
- `--lightpanda PATH` — binary path; if relative, tries CWD then `<pyproject>/../`
- `--resume` / `--out-dir DIR` — resume a prior run

## Grading — notes

`grade.py` reimplements the GAIA paper's rubric (section 3, plus the
upstream `scorer.py`):

- **Strings:** lowercased, articles + punctuation stripped, exact match.
- **Numbers:** commas / units / currency stripped, exact match. Soft
  bucket: ≤1% relative error earns 0.5.
- **Lists:** comma-separated gold; element count must match; per-element
  rule dispatch (number or string); set-match (order doesn't matter).

Unlike AssistantBench's token-F1 / F1-alignment, GAIA is binary per
task. `accuracy_soft` is this port's addition, surfaced so numeric
confabulation doesn't silently score 0.

Frontier agents score ~20–40% on GAIA Level 1 with tool use; base
language models score near 0. Lightpanda's own Level 1 target is
therefore meaningful even in the 10–20% range.
