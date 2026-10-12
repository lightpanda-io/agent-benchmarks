"""
Judge BU Bench V1 predictions with upstream's binary judge.

Each row's tool trace becomes the judge's `agent_steps`, in the shape
browser-use's `AgentHistoryList.agent_steps()` gives it. No screenshots are
sent: the steps carry what the agent read, so they are the judge's only
evidence that an answer came from a page and wasn't made up.

    uv run bubench-grade results/bubench/<ts>/predictions.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..common import emit_scores, mean, read_run_manifest, summarize_usage
from ..leaks import leak_hits
from . import upstream

# `construct_judge_messages` keeps the first 40,000 characters of the joined
# steps, which would drop the end of a long run, where the answer is found.
STEPS_MAX_CHARS = 39_000


def _step_text(i: int, call: dict[str, Any], output: str | None) -> str:
    text = f"Step {i}:\nActions: {json.dumps([{call['tool']: call.get('args')}], indent=1)}\n"
    page = call.get("page")
    if page and page.get("url"):
        text += f"Page: {page['url']}" + (f" ({page['title']})" if page.get("title") else "") + "\n"
    if output:
        text += f"{'Error' if call.get('error') else 'Result'} 1: {output}\n"
    return text


def agent_steps(trace: list[dict[str, Any]]) -> list[str]:
    """Format the tool trace, cutting every result to one common length when
    the whole doesn't fit the judge's window, so no step is dropped."""
    outputs = [call.get("output") or "" for call in trace]

    def build(limit: int) -> list[str]:
        return [
            _step_text(i, call, out[:limit] + ("..." if len(out) > limit else ""))
            for i, (call, out) in enumerate(zip(trace, outputs, strict=True), 1)
        ]

    def size(steps: list[str]) -> int:
        return sum(len(s) + 1 for s in steps)

    longest = max((len(o) for o in outputs), default=0)
    if size(build(longest)) <= STEPS_MAX_CHARS:
        return build(longest)
    lo, hi = 0, longest
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if size(build(mid)) <= STEPS_MAX_CHARS:
            lo = mid
        else:
            hi = mid - 1
    return build(lo)


async def _judge_all(
    rows: list[dict[str, Any]],
    tasks: dict[str, dict[str, Any]],
    *,
    bu_bench_dir: Path,
    judge_model: str,
    concurrency: int,
) -> dict[str, dict[str, Any]]:
    sem = asyncio.Semaphore(concurrency)

    async def one(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        async with sem:
            try:
                verdict = await upstream.judge(
                    bu_bench_dir,
                    task=tasks[row["id"]],
                    final_result=row["prediction"] or "Agent did not return a result",
                    agent_steps=agent_steps(row.get("trace") or []),
                    judge_model=judge_model,
                )
            except Exception as e:
                # A judge failure isn't a task failure: leave it unscored.
                print(f"judge error on {row['id']}: {type(e).__name__}: {e}", file=sys.stderr)
                return row["id"], {"judge_error": f"{type(e).__name__}: {e}"}
            print(f"judged {row['id'][:12]} verdict={verdict['verdict']}", file=sys.stderr)
            return row["id"], verdict

    return dict(await asyncio.gather(*(one(r) for r in rows)))


def grade_predictions(
    predictions_path: Path,
    *,
    bu_bench_dir: Path,
    judge_model: str = upstream.JUDGE_MODEL,
    concurrency: int = 4,
) -> dict[str, Any]:
    rows = [json.loads(line) for line in predictions_path.read_text().splitlines() if line]
    tasks = {t["task_id"]: t for t in upstream.load_tasks(bu_bench_dir)}
    verdicts = asyncio.run(
        _judge_all(
            rows, tasks, bu_bench_dir=bu_bench_dir, judge_model=judge_model, concurrency=concurrency
        )
    )

    task_ids = set(tasks)
    per_task = []
    for r in rows:
        v = verdicts[r["id"]]
        hits = leak_hits(r.get("trace") or [], task_ids, r.get("task"))
        per_task.append(
            {
                "id": r["id"],
                "category": r["category"],
                "score": None if "judge_error" in v else float(v["verdict"]),
                "leak_hits": hits[:5],
                "timed_out": r["timed_out"],
                "duration_s": r["duration_s"],
                "model_calls": r.get("model_calls"),
                "tool_calls": len(r.get("trace") or []),
                "usage": r.get("usage"),
                "judgement": v,
            }
        )

    scored = [t for t in per_task if t["score"] is not None]
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in scored:
        by_category[t["category"]].append(t)

    return {
        **read_run_manifest(predictions_path),
        "bu_bench_commit": upstream.commit(bu_bench_dir),
        "judge_model": judge_model,
        "n_tasks": len(rows),
        "n_scored": len(scored),
        "judge_errors": len(rows) - len(scored),
        "timeouts": sum(1 for t in per_task if t["timed_out"]),
        "accuracy": mean(t["score"] for t in scored),
        "accuracy_leaks_wrong": mean(0.0 if t["leak_hits"] else t["score"] for t in scored),
        "n_leak_flagged": sum(1 for t in per_task if t["leak_hits"]),
        "reached_captcha": sum(1 for t in scored if t["judgement"].get("reached_captcha")),
        "impossible_task": sum(1 for t in scored if t["judgement"].get("impossible_task")),
        "by_category": {
            c: {"n": len(ts), "accuracy": mean(t["score"] for t in ts)}
            for c, ts in sorted(by_category.items())
        },
        "avg_duration_s": mean(t["duration_s"] for t in per_task),
        "avg_tool_calls": mean(t["tool_calls"] for t in per_task),
        **summarize_usage(rows),
        "per_task": per_task,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("predictions", type=Path)
    add_judge_args(parser)
    args = parser.parse_args(argv)
    scores = grade_predictions(
        args.predictions,
        bu_bench_dir=args.bu_bench_dir.resolve(),
        judge_model=args.judge_model,
        concurrency=args.judge_concurrency,
    )
    emit_scores(scores, args.predictions.parent)
    return 0


# benchmarks/src/agent_benchmarks/bubench/grade.py → benchmarks/
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def add_judge_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--bu-bench-dir",
        type=Path,
        default=PROJECT_ROOT / ".bu-bench",
        help="Checkout of github.com/browser-use/benchmark (default: .bu-bench/)",
    )
    parser.add_argument("--judge-model", default=upstream.JUDGE_MODEL)
    parser.add_argument("--judge-concurrency", type=int, default=4)


if __name__ == "__main__":
    sys.exit(main())
