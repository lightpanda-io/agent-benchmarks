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
captured 2026-05-20 with `gemini-3.5-flash`, 4 workers, 600s
per-task timeout.

| Suite | Split | n | Strict | Notes |
|---|---|---:|---:|---|
| AssistantBench | validation | 33 | **63.6%** (soft 55.4%) | Paper's GPT-4 baseline ≈ 25% strict |
| GAIA Level 1 | validation | 53 | **75.5%** | Paper's GPT-4+tools baseline ≈ 30%. Claude 4.5 Sonnet SOTA ≈ 82% |
| WebBench (READ) | one-per-site sample | 448 | **25.4%** | LLM judge: `gemini-3.1-pro-preview` over task + final answer + visited URLs. Not a canonical WebBench number (canonical uses HITL or a multimodal judge). |

GAIA Level 1 includes all 11 attachment tasks: PNG/MP3/PY/TXT fed via
`--attach`; DOCX/XLSX/PPTX extracted to text by the runner first.

Strict counts an answer correct iff its per-task score clears the
suite's threshold (≥ 0.5 for AssistantBench's token-F1; ≡ 1.0 for
GAIA's exact match; YES from the WebBench LLM judge). Each run writes a
`manifest.json` next to `predictions.jsonl` recording the agent
provider/model and the full launch argv, and graders splat that into
`scores.json` for provenance.

Reproducing: `uv run <suite>-run --workers <N>` from the browser repo root
(`--workers 4` recommended to stay under Gemini rate limits).

## Cross-framework comparison: Lightpanda vs agent-browser vs browser-use, Claude+MCP

To isolate the **browser tool surface** as the only variable in a head-to-head
against other agentic browsers, each one is driven by the same Claude Code
session over MCP. The LLM (Claude Sonnet 4.6) is held constant; only the
browser engine + its MCP tool surface differs. Currently wired up:

- **Lightpanda** — built-in `lightpanda mcp` server, text-only, no rendering.
- **[Vercel agent-browser](https://github.com/vercel-labs/agent-browser)** —
  ships no MCP server, so this repo includes a thin wrapper at
  [`competitors/agent-browser-mcp/`](competitors/agent-browser-mcp/) that
  exposes its CDP-style CLI commands as MCP tools.
- **[browser-use](https://github.com/browser-use/browser-use)** — ships its
  own stdio MCP server (`python -m browser_use.mcp.server`), drives full
  Chrome via cdp-use. The runner skips `retry_with_browser_use_agent` (would
  delegate to browser-use's own LLM and contaminate the comparison) and
  `browser_screenshot` (multimodal input, unfair vs the text-only competitors).

Single-run numbers captured 2026-05-19/20 with `claude-sonnet-4-6`, 4 workers,
1800s per-task timeout, OAuth auth (Max subscription), aggressive system
prompt with `<ANSWER>...</ANSWER>` envelope:

| Suite | Lightpanda + Claude+MCP | agent-browser + Claude+MCP | browser-use + Claude+MCP |
|---|---:|---:|---:|
| AssistantBench (33) strict | **66.7%** | **57.6%** | **39.4%** |
| GAIA Level 1 (53) strict | **86.8%** | **84.9%** | **47.2%** |

By AssistantBench difficulty: Medium 85.7% / 85.7% / 71.4% (LP / AB / BU);
Hard 52.6% / 36.8% / 15.8%. The Lightpanda↔agent-browser gap on Hard
(+15.8 pp for LP) is concentrated on multi-source aggregation, where
Lightpanda's `search` / `markdown` / `extract` / `structuredData`
primitives are more efficient than agent-browser's lower-level CDP surface
(`open` / `snapshot` / `click` / `get`).

The browser-use gap on both suites is wider. Two factors visible in the
traces: (1) per-tool latency — `browser_get_state` / `browser_navigate`
return larger payloads and run against full Chrome with extensions,
capping useful tool calls around ~300 per 1800s budget vs ~600–800 for
Lightpanda; (2) the timeout rate is much higher (AB 7/33 = 21%, GAIA
9/53 = 17%), concentrated on the same multi-source aggregation tasks
where agent-browser also struggles but mostly still answers in time.
GAIA tasks that fit a single source-page (Wikipedia, IMDB, Cornell LII)
land well; the multi-hop academic-citation hunts hit the wall.

### How the comparison is locked down

For the Lightpanda-MCP vs agent-browser-MCP comparison to mean anything,
Claude must be restricted to ONLY the browser MCP — no `WebSearch`,
`WebFetch`, `Bash`, `Read`, etc. We learned this the hard way: an earlier
"clean" Lightpanda+MCP run actually used 67× WebSearch + 48× WebFetch + 14×
Bash alongside the MCP tools, inflating scores by 20+ pp. The runner now
enforces:

- `--strict-mcp-config` — blocks other MCP sources (including the user's
  own `~/.claude.json`).
- `--disallowed-tools <comprehensive-list>` — explicit deny-list of every
  built-in Claude Code tool. Lockdown is **load-bearing**: `--allowed-tools`
  alone is *not* a hard restriction in `-p` mode (it's a permission-prompt
  bypass list, not a tool restriction).
- `--system-prompt` (full replace, not append) — replaces Claude Code's
  default tool-use guidance so the model isn't reminded that `Bash` exists.
- Subprocess env scrubbing — strips `ANTHROPIC_API_KEY` so `claude -p` uses
  the keychain OAuth token (Max subscription) instead of API-key billing.

Each prediction row's `trace` field captures every tool call (`{tool, args}`)
from `claude --output-format stream-json` so the lockdown can be audited
per-task. Across the 86 clean tasks: **zero non-MCP tool calls** beyond
`ToolSearch` (the built-in MCP-schema loader Claude Code uses internally;
harmless because it can't bypass `--disallowed-tools`).

### What's comparable and what isn't

| Comparison | Clean? |
|---|---|
| Lightpanda MCP vs agent-browser MCP under the same Claude session | ✓ Yes — same brain, same prompt, same envelope, same parsing |
| Sonnet+MCP vs Flash+native-agent (e.g. 86.8% vs 75.5% GAIA) | ✗ No — three variables change at once: model, agent loop, prompt+envelope |

**Per-model prompt design matters more than absolute prompt quality** —
the published Flash baseline above uses the prompt that's optimal for
Flash (simple "Output ONLY the answer" + early-bail), and the Claude+MCP
numbers use the prompt that's optimal for Sonnet (strict `<ANSWER>`
envelope + persistence guidance).

### Reproducing

```bash
# Prerequisites
npm install -g agent-browser && agent-browser install   # binary + Chrome
export HF_TOKEN=hf_...                                   # GAIA gated dataset
# claude CLI on PATH using Max OAuth — do NOT export ANTHROPIC_API_KEY

LP=/path/to/lightpanda/zig-out/bin/lightpanda

# Lightpanda MCP backend (built-in `lightpanda mcp`)
uv run assistantbench-mcp-run --backend lightpanda --lightpanda $LP \
  --workers 4 --model sonnet --timeout 1800
uv run gaia-mcp-run --backend lightpanda --lightpanda $LP \
  --workers 4 --model sonnet --timeout 1800

# agent-browser MCP backend (wrapper in competitors/agent-browser-mcp/)
uv run assistantbench-mcp-run --backend agent-browser \
  --agent-browser $(which agent-browser) \
  --workers 4 --model sonnet --timeout 1800
uv run gaia-mcp-run --backend agent-browser --workers 4 \
  --model sonnet --timeout 1800

# browser-use MCP backend (built-in `python -m browser_use.mcp.server`).
# Needs `uv sync` (browser-use is a project dep) and a system Chrome/Chromium
# on PATH. Each worker gets its own BROWSER_USE_CONFIG_DIR=/tmp/browser-use-mcp-<sess>
# tempdir for profile isolation (cleaned up at run end).
uv run assistantbench-mcp-run --backend browser-use \
  --workers 4 --model sonnet --timeout 1800
uv run gaia-mcp-run --backend browser-use \
  --workers 4 --model sonnet --timeout 1800
```

Results land in `results/<suite>-mcp/<backend>/<timestamp>/`.

### Running agent-browser standalone (no MCP)

A second comparison path runs agent-browser's own `chat` mode directly, which
uses agent-browser's internal LLM loop instead of Claude. Useful for
comparing against agent-browser-as-shipped:

```bash
# Through Vercel AI Gateway
export AI_GATEWAY_API_KEY=vck_...
uv run assistantbench-ab-run --model google/gemini-3.5-flash
uv run gaia-ab-run --model google/gemini-3.5-flash

# Or direct to Google's OpenAI-compat endpoint (bypasses Vercel)
export GEMINI_DIRECT=1
uv run assistantbench-ab-run --model gemini-3.5-flash
```


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
