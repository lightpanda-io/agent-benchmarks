"""
AssistantBench runner that drives a browser MCP server through `claude -p`.

The point of this runner is the cross-framework comparison: hold the LLM
brain (Claude) constant, hold the dataset and grader constant, vary only
the browser MCP. Pick `--backend lightpanda` or `--backend agent-browser`.

Writes the same predictions.jsonl envelope as `assistantbench-run`, so the
existing `assistantbench-grade` reads it without modification.

Prerequisites:
  - `claude` CLI on PATH (or pass --claude). Subscription auth is fine;
    --bare/ANTHROPIC_API_KEY mode is not required.
  - For --backend lightpanda:  a built lightpanda binary
    (zig-out/bin/lightpanda by default; override via --lightpanda).
  - For --backend agent-browser:  agent-browser installed
    (npm install -g agent-browser && agent-browser install), and the
    `mcp` Python SDK installed in this venv (already a project dep).

Example:

    uv run assistantbench-mcp-run --backend lightpanda --limit 3
    uv run assistantbench-mcp-run --backend agent-browser --workers 2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from datasets import load_dataset  # type: ignore[import-not-found]

from .._mcp import (
    add_common_mcp_args,
    allowed_tools_for,
    build_mcp_config,
    close_agent_browser_sessions,
    drain_pool,
    ensure_executable,
    make_session_pool,
    resolve_agent_browser_bin,
    resolve_claude_bin,
    resolve_lightpanda_bin,
    run_mcp_task,
)
from ..common import (
    emit_scores,
    load_completed_ids,
    resolve_out_dir,
    run_benchmark_tasks,
    write_run_manifest,
)
from .grade import grade_predictions

# benchmarks/src/agent_benchmarks/assistantbench/run_mcp.py → benchmarks/
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# The system prompt fed to Claude. We replace Claude Code's default system
# prompt (via `--system-prompt`, not `--append-`) so the agent isn't
# distracted by Claude Code's tool-use guidance for Bash/Edit/Read. The
# strict-format rules mirror what the Lightpanda and agent-browser-chat
# runners use.
SYSTEM_PROMPT = """\
You are a research assistant driving a headless browser via MCP tools on an open-web QA benchmark.

The browser MCP tools are the ONLY way you can access the web. There is no WebSearch, no WebFetch, no shortcut — you must navigate real pages. Tool surfaces differ per backend (Lightpanda exposes things like `goto`, `markdown`, `findElement`, `search`; agent-browser exposes `open`, `snapshot`, `click @ref`); discover what's available by listing the tools you have.

BE PERSISTENT — this is the load-bearing instruction:
- This benchmark expects multi-step browsing. Most tasks need 20-50 tool calls; some need 100+. Answers from prior knowledge without browsing score 0.
- If a search returns poor results, try DIFFERENT phrasings — synonyms, narrower queries, different angles. Don't repeat the same query.
- If a page is unreachable, find a DIFFERENT source. Wikipedia, official sites, news outlets, Yelp, store directories — be creative.
- If extraction fails, try a different tool (markdown → tree → extract → snapshot).
- Do NOT respond "unknown" or fall back to prior knowledge until you have made at least 20 substantive tool calls AND tried at least 3 different sources/angles.
- Small-candidate questions ("A, B, or C", yes/no): always pick one — never abstain.

Strategy:
1. Plan: identify the most authoritative source. Prefer direct sites (Wikipedia, official, retailer) over search-engine results when you know the source.
2. Search: use the MCP search tool when available, or open https://duckduckgo.com/?q=... when not.
3. Navigate: open the source, inspect, extract.
4. Re-inspect after page-changing actions — DOM snapshots and refs go stale.
5. Cross-check on a second source where the gold answer is non-obvious (lists, numerical estimates).

Final-answer envelope — STRICT
================================
Your entire response will be discarded except for the LAST text wrapped in `<ANSWER>...</ANSWER>` tags. Reasoning, tool-call narration, partial-credit notes — anything outside the envelope — is ignored. Only the envelope contents are graded.

Format INSIDE the envelope:
- No preface, no explanation, no markdown, no source citations.
- Numbers: bare digits only — no units, no `$`, no comma separators.
- Names/titles: complete and verbatim, no decoration.
- URLs: bare URL only.
- Lists: one item per line, nothing else.
- JSON dicts: one JSON object per line, no surrounding prose.

Examples (good):
  <ANSWER>45</ANSWER>
  <ANSWER>Oko, Thief of Crowns</ANSWER>
  <ANSWER>https://example.com/foo</ANSWER>
  <ANSWER>CrossFit East River
  Avea Pilates</ANSWER>

Examples (bad — these all score 0):
  Based on my research, <ANSWER>45</ANSWER>           ← prose outside is fine, but…
  <ANSWER>**45**</ANSWER>                              ← markdown inside
  <ANSWER>The answer is 45.</ANSWER>                   ← preface inside
  <ANSWER>$45</ANSWER>                                 ← currency
  <ANSWER>~45</ANSWER>                                 ← approximation marker
"""

TASK_PROMPT_TEMPLATE = (
    "{task}\n\n"
    "Wrap your final answer in <ANSWER>...</ANSWER>. For a list, one item per line "
    "inside the envelope. For a number, bare digits only inside the envelope."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_mcp_args(parser)
    args = parser.parse_args(argv)

    claude_bin = resolve_claude_bin(args.claude)
    if not ensure_executable(claude_bin):
        print(f"error: claude binary not found at {claude_bin!r}", file=sys.stderr)
        print(
            "hint: install Claude Code (`npm install -g @anthropic-ai/claude-code`) "
            "or pass --claude /path/to/claude.",
            file=sys.stderr,
        )
        return 2

    # Backend-specific binary checks. Each backend needs the *other* binary
    # to be present — Lightpanda binary for the lightpanda backend, the
    # agent-browser binary for the agent-browser backend. Pre-validate so
    # we fail fast instead of N parallel claude failures.
    lightpanda_bin: Path | None = None
    agent_browser_bin: str | None = None
    if args.backend == "lightpanda":
        lightpanda_bin = resolve_lightpanda_bin(args.lightpanda, PROJECT_ROOT)
        if not lightpanda_bin.exists():
            print(f"error: lightpanda binary not found at {lightpanda_bin}", file=sys.stderr)
            print(
                "hint: build with `zig build -Doptimize=ReleaseFast` in the lightpanda repo, "
                "then pass --lightpanda <path> if it isn't at zig-out/bin/lightpanda.",
                file=sys.stderr,
            )
            return 2
    elif args.backend == "agent-browser":
        agent_browser_bin = resolve_agent_browser_bin(args.agent_browser)
        if not ensure_executable(agent_browser_bin):
            print(
                f"error: agent-browser binary not found at {agent_browser_bin!r}",
                file=sys.stderr,
            )
            print(
                "hint: install with `npm install -g agent-browser && agent-browser install`, "
                "or set $AGENT_BROWSER_BIN.",
                file=sys.stderr,
            )
            return 2

    out_dir = resolve_out_dir(args.out_dir, PROJECT_ROOT, f"assistantbench-mcp/{args.backend}")
    predictions_path = out_dir / "predictions.jsonl"
    write_run_manifest(
        out_dir,
        agent_provider=f"claude-mcp-{args.backend}",
        agent_model=args.model,
    )

    completed = load_completed_ids(predictions_path) if args.resume else set()
    if completed:
        print(f"Resuming — skipping {len(completed)} already-completed task(s)", file=sys.stderr)

    print(f"Loading AssistantBench/{args.split} from HuggingFace...", file=sys.stderr)
    ds = load_dataset("AssistantBench/AssistantBench", split=args.split)
    rows: list[dict[str, Any]] = list(ds)
    if args.limit is not None:
        rows = rows[: args.limit]

    pending = [r for r in rows if r["id"] not in completed]

    pool = make_session_pool(args.workers, prefix=f"asb-mcp-{args.backend}")

    def _work(row: dict[str, Any]) -> dict[str, Any]:
        session = pool.get()
        try:
            config = build_mcp_config(
                args.backend,
                lightpanda_bin=lightpanda_bin,
                agent_browser_bin=agent_browser_bin,
                session=session,
            )
            pred, duration_s, timed_out, stderr_tail, rc, trace = run_mcp_task(
                claude_bin=claude_bin,
                backend=args.backend,
                mcp_config=config,
                allowed_tools=allowed_tools_for(args.backend),
                system_prompt=SYSTEM_PROMPT,
                task_prompt=TASK_PROMPT_TEMPLATE.format(task=row["task"]),
                model=args.model,
                timeout_s=args.timeout,
            )
        finally:
            pool.put(session)
        return {
            "id": row["id"],
            "task": row["task"],
            "gold": row["answer"],
            "prediction": pred,
            "duration_s": round(duration_s, 2),
            "timed_out": timed_out,
            "returncode": rc,
            "difficulty": row.get("difficulty"),
            "backend": args.backend,
            "trace": trace,
            "stderr_tail": stderr_tail,
        }

    try:
        run_benchmark_tasks(
            pending,
            _work,
            predictions_path=predictions_path,
            workers=args.workers,
            timeout_s=args.timeout,
            provider=f"claude-mcp-{args.backend}",
            model=args.model,
            preview_fn=lambda row: row["task"],
        )
    finally:
        # Only the agent-browser backend has lingering daemons — Lightpanda's
        # `lightpanda mcp` is a fresh process spawned per claude call and
        # exits when stdio closes.
        if args.backend == "agent-browser" and agent_browser_bin is not None:
            close_agent_browser_sessions(agent_browser_bin, drain_pool(pool))

    print("\nGrading...", file=sys.stderr)
    emit_scores(grade_predictions(predictions_path), out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
