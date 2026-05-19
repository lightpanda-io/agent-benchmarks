"""
AssistantBench runner backed by Vercel agent-browser instead of Lightpanda.

Loads the AssistantBench dataset from HuggingFace, invokes
`agent-browser chat -q -- <message>` one-shot per row through a per-worker
session pool, and writes the same predictions.jsonl envelope as
`assistantbench-run` so the existing grader (`assistantbench-grade`) can score
it without modification.

The point is comparison: hold the dataset, grader, and (ideally) the model
constant; the only thing that varies is the browser engine + its tool surface.

Prerequisites:
  - `agent-browser` on PATH (or pass --agent-browser, or set $AGENT_BROWSER_BIN).
  - AI Gateway credentials: export AI_GATEWAY_API_KEY=gw_...
  - For a like-for-like compare against Lightpanda's published number, pass the
    matching model: `--model anthropic/claude-sonnet-4.6` (or whichever the
    Lightpanda run used).

Example:

    export AI_GATEWAY_API_KEY=gw_...
    uv run assistantbench-ab-run --limit 3
    uv run assistantbench-ab-run --workers 2 --model anthropic/claude-sonnet-4.6
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from datasets import load_dataset  # type: ignore[import-not-found]

from .._agent_browser import (
    add_common_agent_browser_args,
    close_sessions,
    drain_pool,
    ensure_binary,
    gemini_direct_enabled,
    make_session_pool,
    print_agent_browser_missing,
    resolve_agent_browser_binary,
    run_agent_browser_task,
)
from ..common import (
    emit_scores,
    load_completed_ids,
    resolve_out_dir,
    run_benchmark_tasks,
    write_run_manifest,
)
from .grade import grade_predictions

# benchmarks/src/agent_benchmarks/assistantbench/run_ab.py → benchmarks/
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Agent-browser's `chat` ships its own internal system prompt describing the
# CDP tool surface (snapshot/click/fill/find/...), so we don't re-explain
# tool use here — only the suite-specific answer-format rules. This text is
# prepended to the task in a single user message because chat has no
# --system-prompt flag.
SUITE_INSTRUCTIONS = """\
You are a research assistant answering an open-web QA question with the agent-browser tools.

Rules for the final answer (the answer is graded verbatim, character-for-character):
- Output ONLY the answer. No preface ("Based on...", "The answer is...", "Here's..."), no explanation, no caveats, no closing remarks, no source citations.
- No markdown: no **bold**, no *italics*, no `code`, no bullets, no headings, no asterisks anywhere in the final reply.
- Numbers: bare digits only — no units, no `$`, no comma separators, no parenthetical rationale.
- Names/titles: complete and verbatim, no decoration.
- URLs: bare URL only.
- Lists: one item per line, nothing else.
- JSON dicts: one JSON object per line, no surrounding prose.
- DO: `45` / `Oko, Thief of Crowns` / `https://example.com/foo`
- DON'T: `**45**` / `Based on the page, the answer is 45.` / `* 45` / `The card banned was **Oko, Thief of Crowns**.`

Strategy:
- Prefer authoritative direct sources (Wikipedia, official sites, shipping carriers) over search-engine landing pages when you know where to go.
- Small-candidate questions ("A, B, or C", yes/no): always pick one — never abstain.
- Be decisive: aim for ≤10 browser actions per task. If a tool fails repeatedly or the site is unreachable, commit to your best-effort answer from prior knowledge rather than continuing to retry.
- Only respond "unknown" if you have exhausted browsing AND prior knowledge gives no lead.
"""

TASK_PROMPT_TEMPLATE = (
    "{instructions}\n\n"
    "---\n\n"
    "Task: {task}\n\n"
    "Output ONLY the answer — no preface, no explanation, no markdown (no **bold**, no asterisks, "
    "no bullets). For a list, one item per line. For a number, bare digits only."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_agent_browser_args(parser)
    args = parser.parse_args(argv)

    binary = resolve_agent_browser_binary(args.agent_browser)
    if not ensure_binary(binary):
        print_agent_browser_missing(binary)
        return 2

    # API-key validation. agent-browser itself only checks AI_GATEWAY_API_KEY,
    # but in direct-Gemini mode the helper rewrites that key from GOOGLE_API_KEY
    # at subprocess launch — so we validate GOOGLE_API_KEY instead, and fail
    # fast either way to avoid N parallel CLI errors hitting stderr.
    import os

    if gemini_direct_enabled():
        if not os.environ.get("GOOGLE_API_KEY"):
            print(
                "error: GEMINI_DIRECT=1 is set but GOOGLE_API_KEY is empty.\n"
                "  export GOOGLE_API_KEY=...",
                file=sys.stderr,
            )
            return 2
        print(
            "GEMINI_DIRECT=1 — routing agent-browser chat through Google's "
            "OpenAI-compat endpoint (no Vercel AI Gateway).",
            file=sys.stderr,
        )
    elif not os.environ.get("AI_GATEWAY_API_KEY"):
        print(
            "error: AI_GATEWAY_API_KEY is not set — agent-browser chat will refuse to run.\n"
            "  export AI_GATEWAY_API_KEY=gw_...\n"
            "  (or set GEMINI_DIRECT=1 + GOOGLE_API_KEY=... to bypass the gateway)",
            file=sys.stderr,
        )
        return 2

    out_dir = resolve_out_dir(args.out_dir, PROJECT_ROOT, "assistantbench-ab")
    predictions_path = out_dir / "predictions.jsonl"
    write_run_manifest(out_dir, agent_provider="agent-browser", agent_model=args.model)

    completed = load_completed_ids(predictions_path) if args.resume else set()
    if completed:
        print(f"Resuming — skipping {len(completed)} already-completed task(s)", file=sys.stderr)

    print(f"Loading AssistantBench/{args.split} from HuggingFace...", file=sys.stderr)
    ds = load_dataset("AssistantBench/AssistantBench", split=args.split)
    rows: list[dict[str, Any]] = list(ds)
    if args.limit is not None:
        rows = rows[: args.limit]

    pending = [r for r in rows if r["id"] not in completed]

    pool = make_session_pool(args.workers, prefix="ab-bench-asb")

    def _work(row: dict[str, Any]) -> dict[str, Any]:
        session = pool.get()
        try:
            message = TASK_PROMPT_TEMPLATE.format(instructions=SUITE_INSTRUCTIONS, task=row["task"])
            pred, duration_s, timed_out, stderr_tail, rc = run_agent_browser_task(
                binary=binary,
                session=session,
                model=args.model,
                message=message,
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
            # agent-browser's chat doesn't emit the `[tool: ...]` Lightpanda
            # format; trace stays empty so the JSONL envelope still matches.
            "trace": [],
            "stderr_tail": stderr_tail,
        }

    try:
        run_benchmark_tasks(
            pending,
            _work,
            predictions_path=predictions_path,
            workers=args.workers,
            timeout_s=args.timeout,
            provider="agent-browser",
            model=args.model,
            preview_fn=lambda row: row["task"],
        )
    finally:
        # Close the per-worker Chrome instances. Best-effort: if these calls
        # fail, the daemons idle out on their own and we don't want cleanup
        # noise to mask a real failure during the run.
        close_sessions(binary, drain_pool(pool))

    print("\nGrading...", file=sys.stderr)
    emit_scores(grade_predictions(predictions_path), out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
