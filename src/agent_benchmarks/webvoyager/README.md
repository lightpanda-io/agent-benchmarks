# WebVoyager

Drives the Lightpanda agent against the 643-task [WebVoyager](https://github.com/MinorJerry/WebVoyager)
dataset (He et al., ACL 2024) across 15 live sites, and grades each task with
an LLM judge — the shape Browserbase describes in
[*Building Verifiers for Computer Use Agents*](https://www.browserbase.com/blog/building-verifiers-for-computer-use-agents)
and ships in Stagehand as `v3Evaluator`.

## Variant: text-only

The canonical WebVoyager judge (GPT-4V) reads the last N screenshots of the
run. Lightpanda is a text-only browser with no paint/layout pipeline, so this
suite runs a **text-only variant**: the judge sees the task, the agent's
final natural-language answer, the ordered list of URLs visited, and **text
snapshots** of the last few pages the agent observed (outputs of the
`markdown`, `tree`, `extract`, `interactiveElements` tools, tagged by the URL
they were taken on). These excerpts are what grounds the judge's verdict in
the absence of pixels.

Note: Lightpanda's `Terminal.printToolResult` truncates each tool result to
500 chars before writing it to stderr. Snapshots are therefore *excerpts*,
not full page contents. To raise the grounding ceiling, bump
`max_result_display_len` in `src/agent/Terminal.zig` (in the Lightpanda repo)
and rebuild — the parser already handles arbitrary-length bodies.

Implications:

- Numbers are **comparable across Lightpanda runs** with the same
  `judge_model` (Claude Sonnet 4.5 by default) and the same `variant`
  (`"text-only"`).
- Numbers are **not** a canonical WebVoyager leaderboard submission — that
  would require screenshot capture and a multimodal judge. Report as
  "WebVoyager (text-only judge)" when citing.

## Prerequisites

```bash
# Build the lightpanda binary (from the browser repo root)
zig build -Doptimize=ReleaseFast

# Agent provider (default: gemini)
export GOOGLE_API_KEY=...

# Judge provider — set whichever your --judge-model needs.
# Default is claude-sonnet-4-5 (Anthropic); set ANTHROPIC_API_KEY.
# For --judge-model gemini-*, GOOGLE_API_KEY works (shared with the agent).
export ANTHROPIC_API_KEY=...
```

The grader dispatches on the model prefix:

| Prefix | Provider | Env var |
|---|---|---|
| `claude-*` | Anthropic | `ANTHROPIC_API_KEY` |
| `gemini-*` | Google GenAI | `GOOGLE_API_KEY` or `GEMINI_API_KEY` |

## Usage

```bash
# Smoke test — 3 tasks
uv run webvoyager-run --limit 3

# Single site, 4 parallel runs
uv run webvoyager-run --site Allrecipes --workers 4

# Full dataset, then skip inline grading (for example to grade later with a
# different judge)
uv run webvoyager-run --workers 4 --no-grade

# Grade a prior run with a specific judge
uv run webvoyager-grade results/webvoyager/<ts>/predictions.jsonl \
    --judge-model claude-sonnet-4-5

# Or grade with Gemini 3.1 Pro (same-family self-judging: the agent and
# judge share a model family, so treat results with caution)
uv run webvoyager-grade results/webvoyager/<ts>/predictions.jsonl \
    --judge-model gemini-3.1-pro-preview
```

Results land in `results/webvoyager/<UTC-timestamp>/` with
`predictions.jsonl` + `scores.json`. The `scores.json` envelope pins
`judge_model` and `variant` so runs are only comparable when both match.

## Judge prompt

The judge gets a `YES`/`NO` decision framed around three criteria:

1. Does the answer address the task with concrete specifics?
2. Are the visited URLs plausible for the task?
3. Is the answer grounded (named entities, numbers, dates) or generic/hedged?

If reference answers are available (vendored `reference_answer.json` from
the upstream repo), they are passed as a non-authoritative hint — the judge
is told they are one valid answer among possibly many, not a strict-match
target. This mirrors upstream usage.

## Caveats

- LLM judges inflate. Every reported number must be cited together with
  `judge_model`. Swapping the judge breaks comparability.
- Long runs can drop tool-call lines off the 8 KiB stderr tail, but the
  `trace` field is parsed from the full stderr before truncation — so URL
  history survives.
- The subset of sites that require auth (Amazon, Booking) will often return
  partial answers; expect site-level accuracy to vary widely.
