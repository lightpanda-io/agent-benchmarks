"""
WebVoyager runner for the Lightpanda agent.

Loads the 643-task WebVoyager dataset (vendored under `data/`), invokes the
Lightpanda agent one-shot per row with a navigation-oriented system prompt,
and writes predictions + trace (parsed tool calls from the agent's stderr) to
a timestamped results directory. Grading is done separately by
`webvoyager-grade`, which calls an LLM judge over the task + agent answer +
visited URLs.

Unlike GAIA/AssistantBench, WebVoyager has no token-F1 / exact-match gold —
the whole point is that the canonical protocol uses an LLM judge. Reference
answers are loaded as hints and persisted alongside the prediction; the
grader decides how to use them.

Example:

    uv run webvoyager-run --limit 3
    uv run webvoyager-run --site Allrecipes --workers 4
"""

from __future__ import annotations

import argparse
import json
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

# benchmarks/src/agent_benchmarks/webvoyager/run.py → benchmarks/
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = Path(__file__).parent / "data"
TASKS_PATH = DATA_DIR / "WebVoyager_data.jsonl"
REFERENCE_PATH = DATA_DIR / "reference_answer.json"

# WebVoyager expects a short natural-language answer summarizing what the
# agent found, not a terse exact-match string. The judge reads this plus the
# visited URLs to decide if the task was accomplished.
SYSTEM_PROMPT = """\
You are a web navigation assistant driving the Lightpanda browser on a live-web benchmark.

For each task:
1. Start at the URL given in the task. Navigate from there.
2. Use goto, tree, interactiveElements, markdown, extract, findElement to inspect pages and locate information.
3. Answer with a specific, grounded, 1-2 sentence natural-language response that directly addresses the task. Include concrete details (names, numbers, prices, dates, URLs) that you actually saw on the pages you visited.
4. Avoid generic or hedged answers ("I couldn't find...", "it appears that..."). If a site blocks you (cookie wall, 403, access-denied, empty page), say so plainly with a literal description of what you saw, and do NOT fabricate an answer from prior knowledge — an honest "the site blocked access" beats a guessed answer.
5. If a site returns errors or a tool call repeatedly fails, commit to your best-effort answer from what you have gathered rather than thrashing.

Search-engine use:
- For web searches, use the `search` tool — do NOT goto google.com or other search engines directly. With `TAVILY_API_KEY` set, the tool queries the Tavily Search API and returns a clean numbered list of {title, url, snippet}; without the key, it falls back to scraping the DuckDuckGo HTML endpoint. Google scraping is blocked by Lightpanda's User-Agent and TLS fingerprint.

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


def _load_reference_map() -> dict[str, str]:
    """Flatten reference_answer.json into a `{task_id: ref_ans_str}` map.

    Upstream `reference_answer.json` is `{web_name: {notice, answers: [{id,
    type, ans}]}}` where `id` is the int suffix of the task id (e.g. `0` in
    `Allrecipes--0`). Some tasks have multiple possible answers — we join
    them with " / " so the judge sees the full set of acceptable outcomes.
    """
    if not REFERENCE_PATH.exists():
        return {}
    raw = json.loads(REFERENCE_PATH.read_text())
    out: dict[str, str] = {}
    for web_name, entry in raw.items():
        for ans in entry.get("answers", []):
            task_id = f"{web_name}--{ans['id']}"
            text = ans.get("ans")
            if text:
                out[task_id] = out[task_id] + " / " + str(text) if task_id in out else str(text)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_runner_args(parser, suite_name="webvoyager")
    parser.add_argument(
        "--site",
        default=None,
        help="Filter to a single site (e.g. 'Allrecipes', 'GitHub'). Matches web_name exactly.",
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

    out_dir = resolve_out_dir(args.out_dir, PROJECT_ROOT, "webvoyager")
    predictions_path = out_dir / "predictions.jsonl"
    write_run_manifest(out_dir, agent_provider=args.provider, agent_model=args.model)

    completed = load_completed_ids(predictions_path) if args.resume else set()
    if completed:
        print(f"Resuming — skipping {len(completed)} already-completed task(s)", file=sys.stderr)

    rows = _load_tasks()
    reference_map = _load_reference_map()

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
            task_prompt=TASK_PROMPT_TEMPLATE.format(start_url=row["web"], task=row["ques"]),
            timeout_s=args.timeout,
        )
        return {
            "id": row["id"],
            "web_name": row["web_name"],
            "task": row["ques"],
            "start_url": row["web"],
            "reference": reference_map.get(row["id"]),
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
        preview_fn=lambda row: f"[{row['web_name']}] {row['ques']}",
    )

    if args.no_grade:
        print(f"\nSkipped grading (--no-grade). Predictions: {predictions_path}", file=sys.stderr)
        return 0

    print("\nGrading with LLM judge...", file=sys.stderr)
    emit_scores(grade_predictions(predictions_path, variant=VARIANT), out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
