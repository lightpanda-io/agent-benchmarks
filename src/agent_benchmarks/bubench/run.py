"""
BU Bench V1 runner for the Lightpanda agent.

Runs browser-use's 100-task BU Bench V1 (20 each of InteractionTests,
WebBenchREAD, Online-Mind2Web 2, GAIA and BrowseComp) through
`lightpanda agent --task`, one task per process, then judges every run with
upstream's binary judge (see grade.py). The agent gets the task text
verbatim and its default system prompt, as browser-use's agent does, and
searches through Keenable's keyless endpoint.

Needs a checkout of github.com/browser-use/benchmark for the encrypted task
set and the judge, and GOOGLE_API_KEY for the judge.

predictions.jsonl holds decrypted task text and ground truth, which upstream
asks not to publish: results/ stays gitignored.

Example:

    git clone https://github.com/browser-use/benchmark .bu-bench
    uv run bubench-run --model gemini-3.8-flash --block-answer-sources --limit 10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..common import (
    add_common_runner_args,
    answer_source_patterns,
    emit_scores,
    load_completed_ids,
    print_lightpanda_missing,
    resolve_lightpanda_binary,
    resolve_out_dir,
    run_benchmark_tasks,
    run_lightpanda_task,
    write_run_manifest,
)
from . import upstream
from .grade import PROJECT_ROOT, add_judge_args, grade_predictions

# Unset for the agent, so its `search` runs on Keenable's public endpoint:
# no keyed engine to fall back to, and no Keenable key to lift the rate limit.
SEARCH_KEY_ENV = ("BRAVE_API_KEY", "TAVILY_API_KEY", "EXA_API_KEY", "KEENABLE_API_KEY")


def merge_run_trace(calls: list[dict[str, Any]], trace_path: Path) -> int | None:
    """Add the page each tool call left the agent on, its error flag and its
    full result text from the `--trace` file to the stderr-parsed `calls`,
    whose result text the agent's display cuts at 2,000 characters. Returns
    the model call count."""
    try:
        lines = trace_path.read_text().splitlines()
    except OSError:
        return None
    records = [json.loads(line) for line in lines if line.strip()]
    tools = [r for r in records if r.get("type") == "tool"]
    for call, rec in zip(calls, tools, strict=False):
        if rec.get("name") != call["tool"]:
            break
        if rec.get("page"):
            call["page"] = rec["page"]
        if rec.get("error"):
            call["error"] = True
        if rec.get("result") is not None:
            call["output"] = rec["result"]
    return sum(1 for r in records if r.get("type") == "model")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_runner_args(parser, suite_name="bubench")
    add_judge_args(parser)
    parser.add_argument(
        "--category",
        action="append",
        choices=["InteractionTests", "WebBenchREAD", "OM2W2", "GAIA", "BrowseComp"],
        help="Only run this category (repeatable)",
    )
    parser.set_defaults(timeout=1800.0)
    args = parser.parse_args(argv)
    bu_bench_dir = args.bu_bench_dir.resolve()

    lightpanda = resolve_lightpanda_binary(args.lightpanda, PROJECT_ROOT)
    if not lightpanda.exists():
        print_lightpanda_missing(lightpanda)
        return 2

    tasks = upstream.load_tasks(bu_bench_dir)
    if args.category:
        tasks = [t for t in tasks if t["category"] in args.category]
    if args.limit is not None:
        tasks = tasks[: args.limit]

    out_dir = resolve_out_dir(args.out_dir, PROJECT_ROOT, "bubench")
    predictions_path = out_dir / "predictions.jsonl"
    write_run_manifest(out_dir, agent_provider=args.provider, agent_model=args.model)

    completed = load_completed_ids(predictions_path) if args.resume else set()
    if completed:
        print(f"Resuming — skipping {len(completed)} already-completed task(s)", file=sys.stderr)
    pending = [t for t in tasks if t["task_id"] not in completed]
    block_urls = answer_source_patterns(args, [t["task_id"] for t in tasks])
    traces_dir = out_dir / "traces"
    traces_dir.mkdir(exist_ok=True)

    def _work(task: dict[str, Any]) -> dict[str, Any]:
        trace_path = traces_dir / f"{task['task_id']}.jsonl"
        pred, duration_s, timed_out, stderr_tail, rc, trace, usage = run_lightpanda_task(
            lightpanda=lightpanda,
            provider=args.provider,
            model=args.model,
            user_agent=args.user_agent,
            system_prompt=None,
            task_prompt=task["confirmed_task"],
            timeout_s=args.timeout,
            block_urls=block_urls,
            search_engine=args.search_engine,
            trace_path=trace_path,
            drop_env=SEARCH_KEY_ENV,
        )
        return {
            "id": task["task_id"],
            "category": task["category"],
            "task": task["confirmed_task"],
            "gold": task.get("answer"),
            "prediction": pred,
            "duration_s": round(duration_s, 2),
            "timed_out": timed_out,
            "returncode": rc,
            "model_calls": merge_run_trace(trace, trace_path),
            "trace": trace,
            "usage": usage,
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
        preview_fn=lambda t: f"[{t['category']}] {t['confirmed_task']}",
    )

    print("\nJudging...", file=sys.stderr)
    emit_scores(
        grade_predictions(
            predictions_path,
            bu_bench_dir=bu_bench_dir,
            judge_model=args.judge_model,
            concurrency=args.judge_concurrency,
        ),
        out_dir,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
