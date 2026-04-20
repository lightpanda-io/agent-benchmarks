# agent-benchmarks

Benchmark suites for the [Lightpanda](https://github.com/lightpanda-io/browser)
agent. Each suite lives under `src/agent_benchmarks/<suite>/` and ships its
own runner, grader, and README.

## Suites

- [`assistantbench`](src/agent_benchmarks/assistantbench/README.md) — 214
  live-web QA tasks from [Yoran et al., EMNLP 2024](https://arxiv.org/abs/2407.15711).
- [`gaia`](src/agent_benchmarks/gaia/README.md) — [GAIA](https://arxiv.org/abs/2311.12983)
  Level-1 validation split (53 tasks), graded with the paper's
  exact-match rubric. Requires an `HF_TOKEN` (gated dataset). 11 of
  the 53 tasks have attached PDFs/images/audio that Lightpanda can't
  read and will score 0 — matches the published-baseline scope.

## Current results

Lightpanda agent with `gemini-flash-lite-latest` via zenai, 8 workers, default
system prompts. One run per suite; live-web variance at these sample sizes is
roughly ±10 pp per run, so treat single-digit swings as noise.

| Suite | Split | n | Strict | Empty | Notes |
|---|---|---|---|---|---|
| AssistantBench | validation | 33 | **36.4%** | 0 | Paper's GPT-4 baseline ≈ 25% strict |
| GAIA Level 1 | validation | 53 | **26.4%** | 10 | Paper's GPT-4+tools baseline ≈ 30% strict. Includes 11 attachment tasks: PNG/MP3/PY/TXT are fed to the model via `--task-attachment` (2 correct so far); DOCX/XLSX/PPTX are unsupported by zenai's mime table and score 0. |

Strict counts an answer correct iff its per-task score clears the suite's
threshold (≥ 0.5 for AssistantBench's token-F1; ≡ 1.0 for GAIA's exact match).
Empty counts tasks where the agent emitted nothing — this was 10 on GAIA and
1 on AssistantBench before the post-loop synthesis turn was added to the
browser's agent (`src/agent/Agent.zig`); with the fix in place, both are zero.

Reproducing: `uv run <suite>-run --workers 8` from the browser repo root.

## Setup

```bash
# Sync deps and create the venv
uv sync

# Build the lightpanda binary first (run from the browser repo root)
zig build -Doptimize=ReleaseFast

# Set the API key for whichever provider you'll use
export GOOGLE_API_KEY=...
```

## Running

Each suite exposes `<suite>-run` and `<suite>-grade` as console scripts:

```bash
# From the browser repo root (so zig-out/bin/lightpanda resolves)
uv run assistantbench-run --limit 3
uv run assistantbench-grade results/assistantbench/<timestamp>/predictions.jsonl
```

Results land in `results/<suite>/<UTC-timestamp>/`.

## Adding a new suite

1. Create `src/agent_benchmarks/<suite>/` with `__init__.py`, `run.py`,
   `grade.py`, and a `README.md`.
2. Expose console scripts in `pyproject.toml` under `[project.scripts]`:
   `<suite>-run = "agent_benchmarks.<suite>.run:main"` and
   `<suite>-grade = "agent_benchmarks.<suite>.grade:main"`.
3. `uv sync` to re-resolve.

## Development

```bash
uv run ruff check src/
uv run ruff format src/
```
