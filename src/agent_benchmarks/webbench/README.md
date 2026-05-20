# WebBench

Drives the Lightpanda agent against the READ subset of
[WebBench](https://github.com/Halluminate/WebBench) (Halluminate × Skyvern,
2,454 open-sourced tasks across 452 live websites — an explicit successor to
WebVoyager that scales sites 15→452 and tasks 642→5,750). Tasks are graded by
a text-only LLM judge; the judge implementation lives in the shared
[`agent_benchmarks.llm_judge`](../llm_judge.py) module.

## Variant: text-only, READ subset only

WebBench has five categories — READ (64.4%), CREATE (20.9%), UPDATE (7.1%),
DELETE (6.1%), FILE_MANIPULATION (1.5%). This suite vendors **only the READ
subset** (1,637 tasks across 448 sites), for two reasons:

1. The write-style categories require real account auth on 452 different
   sites — there's no realistic way to provision credentials at that scale.
2. CREATE/UPDATE/DELETE produce side effects on live sites that an LLM judge
   can't verify from text snapshots — it could only check whether the agent
   *claimed* to perform the action.

Even within READ, "text-only" is a modality assumption, not a
guarantee — `Category=READ` describes the data action. A small minority
of READ tasks bake in interactions a text browser can't do (e.g. "play
the audio sample" on dictionary.com); these will simply count as
failures.

The judge sees the task, the agent's final natural-language answer, the
ordered list of URLs visited, and **text snapshots** (truncated outputs
of the `markdown` / `tree` / `extract` / `interactiveElements` tools).

Implications:

- Numbers are **comparable across Lightpanda runs** with the same
  `judge_model` (Claude Sonnet 4.5 by default) and `variant`
  (`"webbench-text-only"`).
- Numbers are **not** a canonical WebBench leaderboard submission — that
  would require either HITL verification or a multimodal judge over
  screenshots.

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

## Usage

```bash
# Smoke test — 3 tasks
uv run webbench-run --limit 3

# Single site, 4 parallel runs (web_name = host with www. stripped)
uv run webbench-run --site allrecipes.com --workers 4

# Full READ subset (1,637 tasks, ~3.3h with 4 workers on gemini-3.5-flash)
# Skip inline grading so you can re-grade later with a different judge.
uv run webbench-run --workers 4 --no-grade

# Grade a prior run with a specific judge
uv run webbench-grade results/webbench/<ts>/predictions.jsonl \
    --judge-model claude-sonnet-4-5
```

Results land in `results/webbench/<UTC-timestamp>/` with `predictions.jsonl`
and `scores.json`. `scores.json` pins `judge_model` and `variant` so runs
are only comparable when both match.

## Refreshing the vendored dataset

The `data/webbench_read.jsonl` is a snapshot of the HF parquet
(`Halluminate/WebBench`, split `train`) filtered to `Category == "READ"`.
To regenerate:

```bash
uv run python -c "
import json
from urllib.parse import urlparse
from datasets import load_dataset

def web_name(url):
    h = (urlparse(url).hostname or '').lower()
    return h[4:] if h.startswith('www.') else h

ds = load_dataset('Halluminate/WebBench', split='train')
with open('src/agent_benchmarks/webbench/data/webbench_read.jsonl', 'w') as f:
    for r in ds:
        if r['Category'] != 'READ':
            continue
        f.write(json.dumps({
            'id': f'webbench-{r[\"ID\"]}',
            'start_url': r['Starting URL'],
            'web_name': web_name(r['Starting URL']),
            'category': r['Category'],
            'task': r['Task'],
        }) + '\n')
"
```

## Caveats

- LLM judges inflate. Every reported number must be cited together with
  `judge_model`. Swapping the judge breaks comparability.
- WebBench task strings include a "stay on this site" constraint that the
  agent must respect — judge prompts don't enforce it, but going off-site
  usually shows up as low-quality URL traces.
- Sites that require auth (Amazon, Booking, social login) often return
  partial answers even on READ tasks; expect site-level accuracy to vary
  widely. The per-site breakdown in `scores.json` will surface this.
