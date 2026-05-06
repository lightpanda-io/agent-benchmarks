"""
AssistantBench runner for the Lightpanda agent.

Loads the AssistantBench dataset from HuggingFace, invokes the `lightpanda
agent --task` one-shot mode per row, captures stdout as the predicted answer,
and writes predictions + scores to a timestamped results directory.

Example:

    # Smoke test
    uv run assistantbench-run --limit 3

    # Full dev run
    uv run assistantbench-run --split validation
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from datasets import load_dataset  # type: ignore[import-not-found]

from ..common import (
    add_common_runner_args,
    emit_scores,
    load_completed_ids,
    print_lightpanda_missing,
    resolve_lightpanda_binary,
    resolve_out_dir,
    run_benchmark_tasks,
    run_lightpanda_task,
)
from .grade import grade_predictions

# benchmarks/src/agent_benchmarks/assistantbench/run.py → benchmarks/
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Override Agent.zig's tool-use-oriented default with a research-oriented
# system prompt tailored to the benchmark. Preserves the load-bearing CSS
# selector rules so tool use still works.
SYSTEM_PROMPT = """\
You are a research assistant driving the Lightpanda browser on an open-web QA benchmark.

For each task:
1. Plan: identify the most authoritative source (official website, known database, or a search engine). Prefer direct sites (IMDB, Wikipedia, shipping carriers, official brand pages) over search engines when you know the source.
2. Navigate: use search, goto, tree, markdown, extract, findElement to inspect pages.
3. Final answer format — your entire last message is graded verbatim, character-for-character:
   - Output ONLY the answer. No preface ("Based on...", "The answer is...", "Here's..."), no explanation, no caveats, no closing remarks, no source citations.
   - No markdown: no **bold**, no *italics*, no `code`, no bullets, no headings, no asterisks anywhere in the final reply.
   - Numbers: bare digits only — no units, no `$`, no comma separators, no parenthetical rationale.
   - Names/titles: complete and verbatim, no decoration.
   - URLs: bare URL only.
   - Lists: one item per line, nothing else.
   - JSON dicts: one JSON object per line, no surrounding prose.
   - DO: `45` / `Oko, Thief of Crowns` / `https://example.com/foo`
   - DON'T: `**45**` / `Based on the page, the answer is 45.` / `* 45 (calculated as 4 × $12)` / `The card banned was **Oko, Thief of Crowns**.`
4. Small-candidate questions ("A, B, or C", yes/no): always pick one — never abstain.
5. Be decisive. Target ≤10 tool calls per task. If you hit that budget, or a site returns errors, or a tool call repeatedly fails, or you cannot extract the needed info, commit to your best-effort answer from prior knowledge rather than continuing to search. Model knowledge is a valid last resort — preferable to no answer.
6. Only respond "unknown" if you have exhausted navigation AND prior knowledge gives no lead.

Search-engine use:
- For web searches, use the `search` tool — do NOT goto google.com or other search engines directly. With `TAVILY_API_KEY` set, the tool queries the Tavily Search API and returns a clean numbered list of {title, url, snippet}; without the key, it falls back to scraping the DuckDuckGo HTML endpoint. Google scraping is blocked by Lightpanda's User-Agent and TLS fingerprint.

Tool-use rules:
- Never use backendNodeId with click, fill, hover, selectOption, or setChecked. Always use a CSS selector.
- Use findElement to resolve a description into a selector when needed.
- Use distinguishing attributes (value, name, position) so selectors are unique.
- For credentials, pass $LP_USERNAME / $LP_PASSWORD directly as values.
"""

TASK_PROMPT_TEMPLATE = (
    "{task}\n\n"
    "Output ONLY the answer — no preface, no explanation, no markdown (no **bold**, no asterisks, "
    "no bullets). For a list, one item per line. For a number, bare digits only."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_runner_args(parser, suite_name="assistantbench")
    args = parser.parse_args(argv)

    lightpanda = resolve_lightpanda_binary(args.lightpanda, PROJECT_ROOT)
    if not lightpanda.exists():
        print_lightpanda_missing(lightpanda)
        return 2

    out_dir = resolve_out_dir(args.out_dir, PROJECT_ROOT, "assistantbench")
    predictions_path = out_dir / "predictions.jsonl"

    completed = load_completed_ids(predictions_path) if args.resume else set()
    if completed:
        print(f"Resuming — skipping {len(completed)} already-completed task(s)", file=sys.stderr)

    print(f"Loading AssistantBench/{args.split} from HuggingFace...", file=sys.stderr)
    ds = load_dataset("AssistantBench/AssistantBench", split=args.split)
    rows: list[dict[str, Any]] = list(ds)
    if args.limit is not None:
        rows = rows[: args.limit]

    pending = [r for r in rows if r["id"] not in completed]

    def _work(row: dict[str, Any]) -> dict[str, Any]:
        pred, duration_s, timed_out, stderr_tail, rc, trace = run_lightpanda_task(
            lightpanda=lightpanda,
            provider=args.provider,
            model=args.model,
            user_agent=args.user_agent,
            system_prompt=SYSTEM_PROMPT,
            task_prompt=TASK_PROMPT_TEMPLATE.format(task=row["task"]),
            timeout_s=args.timeout,
        )
        return {
            "id": row["id"],
            "task": row["task"],
            "gold": row["answer"],
            "prediction": pred,
            "duration_s": round(duration_s, 2),
            "timed_out": timed_out,
            "returncode": rc,
            "difficulty": row.get("difficulty"),
            "trace": trace,
            "stderr_tail": stderr_tail,
        }

    run_benchmark_tasks(
        pending,
        _work,
        predictions_path=predictions_path,
        workers=args.workers,
        timeout_s=args.timeout,
        provider=args.provider,
        model=args.model,
        preview_fn=lambda row: row["task"],
    )

    print("\nGrading...", file=sys.stderr)
    emit_scores(grade_predictions(predictions_path), out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
