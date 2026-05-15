"""
WebBench runner for the Lightpanda agent.

Loads the vendored READ subset of [WebBench](https://huggingface.co/datasets/Halluminate/WebBench)
(1,637 tasks across ~448 sites — the data-extraction half of the 2,454-task
benchmark; CREATE/UPDATE/DELETE/FILE_MANIPULATION are out of scope for a
text-only browser, see README.md), invokes the Lightpanda agent one-shot per
row with a navigation-oriented system prompt, and writes predictions + trace
to a timestamped results directory. Grading defers to
`agent_benchmarks.llm_judge` with `variant="webbench-text-only"`.

WebBench has no reference answers in the dataset; the upstream protocol is
human-in-the-loop. We run a text-only LLM-judge approach — comparable
across Lightpanda runs with the same judge_model and variant, but **not**
a canonical WebBench leaderboard number (canonical uses HITL or a
multimodal judge over screenshots).

Example:

    uv run webbench-run --limit 3
    uv run webbench-run --site allrecipes.com --workers 4
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

from ..common import (
    add_common_runner_args,
    emit_scores,
    load_completed_ids,
    print_lightpanda_missing,
    resolve_lightpanda_binary,
    resolve_out_dir,
    run_benchmark_tasks,
    run_lightpanda_task,
    write_run_manifest,
)
from ..llm_judge import grade_predictions
from .grade import VARIANT

# benchmarks/src/agent_benchmarks/webbench/run.py → benchmarks/
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = Path(__file__).parent / "data"
TASKS_PATH = DATA_DIR / "webbench_read.jsonl"

# Short, grounded natural-language answers. The WebBench task strings
# already include the "only use http://X.com" constraint, so we don't
# need to repeat it here.
SYSTEM_PROMPT = """\
You are a web navigation assistant driving the Lightpanda browser on a live-web benchmark.

For each task:
1. Start at the URL given in the task. Navigate from there.
2. Use goto, tree, interactiveElements, markdown, extract, findElement to inspect pages and locate information.
3. Answer with a specific, grounded, 1-2 sentence natural-language response that directly addresses the task. Include concrete details (names, numbers, prices, dates, URLs) that you actually saw on the pages you visited.
4. Avoid generic or hedged answers ("I couldn't find...", "it appears that..."). If a site blocks you (cookie wall, 403, access-denied, empty page), say so plainly with a literal description of what you saw, and do NOT fabricate an answer from prior knowledge — an honest "the site blocked access" beats a guessed answer.
5. If a site returns errors or a tool call repeatedly fails, commit to your best-effort answer from what you have gathered rather than thrashing.

Domain constraint:
- WebBench tasks specify a single domain ("Only use http://X.com — don't go to any other site"). Do NOT use the `search` tool and do NOT goto google.com or any search engine. Stay on the constrained domain throughout the task.
- If you cannot find the information by navigating within the domain, answer with a literal description of what you saw rather than fabricating from off-domain sources.

Tool-use rules:
- Never use backendNodeId with click, fill, hover, selectOption, or setChecked. Always use a CSS selector.
- Use findElement to resolve a description into a selector when needed.
- Use distinguishing attributes (value, name, position) so selectors are unique.
- For credentials, pass $LP_USERNAME / $LP_PASSWORD directly as values.
"""

TASK_PROMPT_TEMPLATE = (
    "Starting URL: {start_url}\n"
    "Task: {task}\n\n"
    "Accomplish the task by navigating the web. Answer in 1-2 sentences with "
    "specific details you actually saw on the pages you visited."
)


def _load_tasks() -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    with TASKS_PATH.open() as f:
        for line in f:
            line = line.strip()
            if line:
                tasks.append(json.loads(line))
    return tasks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_runner_args(parser, suite_name="webbench")
    parser.add_argument(
        "--site",
        default=None,
        help="Filter to a single site (e.g. 'allrecipes.com', 'github.com'). "
        "Matches web_name (URL host, lowercased, www. stripped) exactly.",
    )
    parser.add_argument(
        "--sample-per-site",
        type=int,
        default=None,
        help="Randomly sample at most N task(s) per site. Useful for broad-coverage runs "
        "in a fixed wall-clock budget (e.g. --sample-per-site 1 → ~448 tasks).",
    )
    parser.add_argument(
        "--sample-seed",
        type=int,
        default=0,
        help="Seed for --sample-per-site (default: 0, reproducible).",
    )
    parser.add_argument(
        "--no-grade",
        action="store_true",
        help="Skip the grading step after running. Useful when you want to run multiple "
        "times and grade later, or grade with a different judge.",
    )
    args = parser.parse_args(argv)

    lightpanda = resolve_lightpanda_binary(args.lightpanda, PROJECT_ROOT)
    if not lightpanda.exists():
        print_lightpanda_missing(lightpanda)
        return 2

    if not TASKS_PATH.exists():
        print(f"error: missing vendored dataset at {TASKS_PATH}", file=sys.stderr)
        return 2

    out_dir = resolve_out_dir(args.out_dir, PROJECT_ROOT, "webbench")
    predictions_path = out_dir / "predictions.jsonl"
    write_run_manifest(out_dir, agent_provider=args.provider, agent_model=args.model)

    completed = load_completed_ids(predictions_path) if args.resume else set()
    if completed:
        print(f"Resuming — skipping {len(completed)} already-completed task(s)", file=sys.stderr)

    rows = _load_tasks()

    if args.site:
        before = len(rows)
        rows = [r for r in rows if r["web_name"] == args.site]
        print(f"Filtered to site '{args.site}': {before} → {len(rows)} task(s)", file=sys.stderr)
        if not rows:
            print(
                f"error: no tasks match --site {args.site}. Available sites:",
                file=sys.stderr,
            )
            sites = sorted({r["web_name"] for r in _load_tasks()})
            for s in sites:
                print(f"  {s}", file=sys.stderr)
            return 2

    if args.sample_per_site is not None:
        rng = random.Random(args.sample_seed)
        by_site: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by_site.setdefault(r["web_name"], []).append(r)
        sampled: list[dict[str, Any]] = []
        for _, group in sorted(by_site.items()):
            rng.shuffle(group)
            sampled.extend(group[: args.sample_per_site])
        before = len(rows)
        rows = sampled
        print(
            f"Per-site sample (n={args.sample_per_site}, seed={args.sample_seed}): "
            f"{before} → {len(rows)} task(s) across {len(by_site)} site(s)",
            file=sys.stderr,
        )

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
            task_prompt=TASK_PROMPT_TEMPLATE.format(start_url=row["start_url"], task=row["task"]),
            timeout_s=args.timeout,
        )
        return {
            "id": row["id"],
            "web_name": row["web_name"],
            "category": row["category"],
            "task": row["task"],
            "start_url": row["start_url"],
            "reference": None,
            "prediction": pred,
            "duration_s": round(duration_s, 2),
            "timed_out": timed_out,
            "returncode": rc,
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
        preview_fn=lambda row: f"[{row['web_name']}] {row['task'].splitlines()[0]}",
    )

    if args.no_grade:
        print(f"\nSkipped grading (--no-grade). Predictions: {predictions_path}", file=sys.stderr)
        return 0

    print("\nGrading with LLM judge...", file=sys.stderr)
    emit_scores(grade_predictions(predictions_path, variant=VARIANT), out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
