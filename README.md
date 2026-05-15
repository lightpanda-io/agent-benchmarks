# agent-benchmarks

Benchmark suites for the [Lightpanda](https://github.com/lightpanda-io/browser)
agent. Each suite lives under `src/agent_benchmarks/<suite>/` and ships its
own runner, grader, and README.

## Suites

- [`assistantbench`](src/agent_benchmarks/assistantbench/README.md) — 214
  live-web QA tasks from [Yoran et al., EMNLP 2024](https://arxiv.org/abs/2407.15711).
  We run the 33-task validation split.
- [`gaia`](src/agent_benchmarks/gaia/README.md) — [GAIA](https://arxiv.org/abs/2311.12983)
  Level-1 validation split (53 tasks), graded with the paper's
  exact-match rubric. Requires an `HF_TOKEN` (gated dataset). 11 of
  the 53 tasks have attached PDFs/images/audio that Lightpanda can't
  read and will score 0 — matches the published-baseline scope.
- [`webbench`](src/agent_benchmarks/webbench/README.md) —
  [WebBench](https://github.com/Halluminate/WebBench) (Halluminate × Skyvern),
  READ subset only (1,637 tasks across 448 sites). Text-only LLM judge
  ([`llm_judge`](src/agent_benchmarks/llm_judge.py)) in the shape of
  Browserbase's [`v3Evaluator`](https://www.browserbase.com/blog/building-verifiers-for-computer-use-agents),
  judging task + agent answer + visited URLs (no screenshots). Default
  sample is one task per site (448 tasks). CREATE/UPDATE/DELETE/
  FILE_MANIPULATION categories are out of scope (need real account auth
  on every site, side effects unverifiable from text snapshots).

## Current results

Lightpanda agent via zenai, default system prompts. Single-run numbers
captured 2026-05-14 with `gemini-3-flash-preview`, 4 workers, 600s
per-task timeout.

| Suite | Split | n | Strict | Notes |
|---|---|---:|---:|---|
| AssistantBench | validation | 33 | **48.5%** (soft 45.4%) | Paper's GPT-4 baseline ≈ 25% strict |
| GAIA Level 1 | validation | 53 | **62.3%** | Paper's GPT-4+tools baseline ≈ 30%. Claude 4.5 Sonnet SOTA ≈ 82% |
| WebBench (READ) | one-per-site sample | 448 | **23.2%** | LLM judge: `gemini-3.1-pro-preview` over task + final answer + visited URLs. Not a canonical WebBench number (canonical uses HITL or a multimodal judge). |

GAIA Level 1 includes all 11 attachment tasks: PNG/MP3/PY/TXT fed via
`--task-attachment`; DOCX/XLSX/PPTX extracted to text by the runner first.

Strict counts an answer correct iff its per-task score clears the
suite's threshold (≥ 0.5 for AssistantBench's token-F1; ≡ 1.0 for
GAIA's exact match; YES from the WebBench LLM judge). Each run writes a
`manifest.json` next to `predictions.jsonl` recording the agent
provider/model and the full launch argv, and graders splat that into
`scores.json` for provenance.

Reproducing: `uv run <suite>-run --workers <N>` from the browser repo root
(`--workers 4` recommended for flash-preview to stay under Gemini rate limits).

## Comparability across frameworks

Of the three suites, only **GAIA** and **AssistantBench-strict** produce
numbers that can be compared directly to other frameworks' published
results — both grade with deterministic rubrics from their respective
papers (exact-match / token-F1) over a fixed gold answer.

WebBench is in the suite for internal Lightpanda comparison, not
cross-framework comparison. Its canonical evaluation is
human-in-the-loop: per the
[Halluminate technical report](https://halluminate.ai/blog/benchmark),
"In order to evaluate the results of each agent trajectory, we employ a
team of human annotators" who review the task, the agent's output, and
a screen recording. There is no published text-only LLM-judge protocol,
so our `webbench-text-only` variant is something we made up. It is
comparable across Lightpanda runs that share `judge_model` and
`variant`; it is *not* comparable to any number on
[webbench.ai](https://webbench.ai/) or in another framework's blog.

### Why WebBench (READ) but not WebVoyager

[WebVoyager](https://github.com/MinorJerry/WebVoyager)'s canonical
grader is GPT-4V over the last *k* screenshots of the trajectory, per
their README: "We provide the task, the responses from WebVoyager, and
the last k screenshots to the GPT-4V and ask it to judge whether the
agent has successfully completed the task." Lightpanda has no
rendering, so we can't run that grader. WebVoyager does ship a
`--text_only` *agent* mode, but there is no documented text-only
*evaluation* variant — only the multimodal one.

The thing that makes WebBench's READ subset still useful under a
text-only grader, where WebVoyager would not be, is what the tasks
*ask* the agent to produce:

- **WebBench READ tasks** are data-extraction questions ("what is the
  price of X on this site?", "which results are listed on this page?").
  The agent's deliverable is a textual answer, and that answer alone is
  what HITL annotators check too. Halluminate reports top automated
  agents at >75% on READ vs only 46.6% on non-READ — separating READ
  out is something the canonical evaluation already does.
- **WebVoyager tasks** are mostly navigation / end-state tasks
  ("subscribe to this newsletter", "find and add this item to cart").
  The deliverable is a UI state, not a string, and the canonical
  GPT-4V grader checks the screenshots to see whether that state was
  actually reached. A text-only judge looking at "task + answer +
  visited URLs" can only confirm that the agent *claims* it did the
  thing — not whether the page reflects it.

So for WebBench READ, the text-only judge is a defensible (if
non-canonical) approximation of what HITL would check on the same
trajectory. For WebVoyager it would be a meaningful step away from
what the canonical grader actually scores. Combined with the
redundancy (same judge shape, fewer tasks, narrower site set than
WebBench: 643 / 15 vs 1,637 / 448), there was no reason to keep it.

If you want a number that *is* canonical-comparable to a published
WebVoyager leaderboard, you'd need to either render screenshots
(out of scope for Lightpanda) or import another framework's exact
text-only judge prompt as a new `llm_judge` variant.

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
