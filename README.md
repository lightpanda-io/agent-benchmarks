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
- [`webvoyager`](src/agent_benchmarks/webvoyager/README.md) —
  [WebVoyager](https://github.com/MinorJerry/WebVoyager) (He et al., ACL 2024),
  643 live-web tasks across 15 sites, graded by an LLM judge in the shape of
  Browserbase's [`v3Evaluator`](https://www.browserbase.com/blog/building-verifiers-for-computer-use-agents).
  Text-only variant — the judge sees the task, the agent's final answer, and
  the list of URLs it visited (no screenshots). Requires
  `ANTHROPIC_API_KEY` for the Claude judge.

## Current results

Lightpanda agent via zenai, default system prompts. Numbers below are
means over 4 runs captured on 2026-04-22 with `gemini-3-flash-preview`,
4 workers, 600s per-task timeout. Single-run variance is ±4–6 pp, so
treat mean differences below that as noise; prefer re-running rather
than reading into a single comparison.

| Suite | Model | Split | n | Runs | Strict (mean ± stdev) | Best run | Notes |
|---|---|---|---:|---:|---:|---:|---|
| AssistantBench | `gemini-3-flash-preview` | validation | 33 | 4 | **61.4% ± 6.2 pp** | 69.7% | Paper's GPT-4 baseline ≈ 25% strict |
| GAIA Level 1 | `gemini-3-flash-preview` | validation | 53 | 4 | **68.4% ± 4.2 pp** | 73.6% | Paper's GPT-4+tools baseline ≈ 30%. Claude 4.5 Sonnet SOTA ≈ 82% |
| WebVoyager | `gemini-3-flash-preview` | full | 643 | — | *(run pending)* | — | Text-only judge variant: `claude-sonnet-4-5` over task + final answer + visited URLs. Not a canonical WebVoyager leaderboard number (canonical uses screenshots). |

GAIA Level 1 includes all 11 attachment tasks: PNG/MP3/PY/TXT fed via
`--task-attachment`; DOCX/XLSX/PPTX extracted to text by the runner first.

Strict counts an answer correct iff its per-task score clears the suite's
threshold (≥ 0.5 for AssistantBench's token-F1; ≡ 1.0 for GAIA's exact match).
Empty answers and timeouts are both zero in practice after two recent
agent/transport changes: a post-loop synthesis turn in
`src/agent/Agent.zig` (forces a final answer when the tool-use loop
exhausts), and automatic retry of transient HTTP failures in zenai
(5xx, 429, and known flaky network errors). Earlier single-run numbers
predate those and aren't directly comparable.

Reproducing: `uv run <suite>-run --workers <N>` from the browser repo root
(`--workers 4` recommended for flash-preview to stay under Gemini rate limits).

## Setup

```bash
# Sync deps and create the venv
uv sync

# Build the lightpanda binary first (run from the browser repo root)
zig build -Doptimize=ReleaseFast

# Set the API key for whichever provider you'll use
export GOOGLE_API_KEY=...

# Strongly recommended: route the lightpanda `search` tool through Tavily.
# When unset, search falls back to scraping DuckDuckGo's HTML endpoint —
# noisier results, may rate-limit under concurrency. Google scraping was
# tried and dropped because Lightpanda's User-Agent and TLS fingerprint
# get blocked or fed a consent wall on essentially every query.
export TAVILY_API_KEY=tvly-...
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
