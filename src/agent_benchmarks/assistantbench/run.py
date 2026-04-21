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
import concurrent.futures
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from datasets import load_dataset  # type: ignore[import-not-found]

from ..common import (
    load_completed_ids,
    print_lightpanda_missing,
    resolve_lightpanda_binary,
    run_lightpanda_task,
    status_label,
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
2. Navigate: use goto, tree, markdown, extract, findElement to inspect pages.
3. Answer terse: output only the answer. For lists, one item per line. For numbers, the bare number (no units, no prose). For URLs, the bare URL.
4. Small-candidate questions ("A, B, or C", yes/no): always pick one — never abstain.
5. Be decisive. Target ≤10 tool calls per task. If you hit that budget, or a site returns errors, or a tool call repeatedly fails, or you cannot extract the needed info, commit to your best-effort answer from prior knowledge rather than continuing to search. Model knowledge is a valid last resort — preferable to no answer.
6. Only respond "unknown" if you have exhausted navigation AND prior knowledge gives no lead.

Search-engine use:
- When using Google, always include &hl=en&gl=us in the URL (e.g. https://www.google.com/search?q=...&hl=en&gl=us) to bypass localized consent pages.

Tool-use rules:
- Never use backendNodeId with click, fill, hover, selectOption, or setChecked. Always use a CSS selector.
- Use findElement to resolve a description into a selector when needed.
- Use distinguishing attributes (value, name, position) so selectors are unique.
- For credentials, pass $LP_USERNAME / $LP_PASSWORD directly as values.
"""

TASK_PROMPT_TEMPLATE = (
    "{task}\n\n"
    "Output only the answer (terse). For a list, one item per line. No explanation, no markdown."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="validation", choices=["validation", "test"])
    parser.add_argument("--limit", type=int, default=None, help="Run at most N tasks")
    parser.add_argument("--workers", type=int, default=1, help="Parallel lightpanda subprocesses")
    parser.add_argument("--timeout", type=float, default=300.0, help="Per-task timeout in seconds")
    parser.add_argument(
        "--lightpanda",
        type=Path,
        default=Path("zig-out/bin/lightpanda"),
        help="Path to the lightpanda binary. If relative, tries CWD then <pyproject>/../ (default: zig-out/bin/lightpanda)",
    )
    parser.add_argument(
        "--provider", default="gemini", help="Lightpanda agent provider (default: gemini)"
    )
    parser.add_argument("--model", default=None, help="Override default model for the provider")
    parser.add_argument(
        "--user-agent",
        default=None,
        help="Override the browser User-Agent (forwarded to lightpanda --user-agent). "
        'Cannot contain "Mozilla" per Lightpanda\'s policy.',
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output dir (default: results/assistantbench/<timestamp>/)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip task ids already present in <out-dir>/predictions.jsonl",
    )
    args = parser.parse_args(argv)

    lightpanda = resolve_lightpanda_binary(args.lightpanda, PROJECT_ROOT)
    if not lightpanda.exists():
        print_lightpanda_missing(lightpanda)
        return 2

    if args.out_dir is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = PROJECT_ROOT / "results" / "assistantbench" / ts
    else:
        out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
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
    print(
        f"Running {len(pending)} task(s) with {args.workers} worker(s), "
        f"timeout {args.timeout:.0f}s, provider={args.provider}"
        + (f", model={args.model}" if args.model else ""),
        file=sys.stderr,
    )
    print(f"Output dir: {out_dir}", file=sys.stderr)

    results_lock_file = predictions_path.open("a")

    def _work(row: dict[str, Any]) -> dict[str, Any]:
        pred, duration_s, timed_out, stderr_tail, rc = run_lightpanda_task(
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
            "stderr_tail": stderr_tail,
        }

    try:
        if args.workers <= 1:
            for idx, row in enumerate(pending, 1):
                result = _work(row)
                results_lock_file.write(json.dumps(result) + "\n")
                results_lock_file.flush()
                print(
                    f"[{idx}/{len(pending)}] {status_label(result)} {result['duration_s']:.1f}s {result['id'][:12]} — {row['task'][:80]}",
                    file=sys.stderr,
                )
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
                futures = {ex.submit(_work, row): row for row in pending}
                done = 0
                for fut in concurrent.futures.as_completed(futures):
                    result = fut.result()
                    results_lock_file.write(json.dumps(result) + "\n")
                    results_lock_file.flush()
                    done += 1
                    print(
                        f"[{done}/{len(pending)}] {status_label(result)} {result['duration_s']:.1f}s {result['id'][:12]}",
                        file=sys.stderr,
                    )
    finally:
        results_lock_file.close()

    print("\nGrading...", file=sys.stderr)
    scores = grade_predictions(predictions_path)
    scores_path = out_dir / "scores.json"
    scores_path.write_text(json.dumps(scores, indent=2))

    summary = {k: v for k, v in scores.items() if k != "per_task"}
    print(json.dumps(summary, indent=2))
    print(f"\nResults: {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
