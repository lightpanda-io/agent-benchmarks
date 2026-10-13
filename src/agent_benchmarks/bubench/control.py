"""
Re-judge an upstream Browser Use run of BU Bench V1 the way bubench-grade
judges Lightpanda's: same judge and model, no screenshots.

Upstream's `run_framework_eval.py` judges each task with the agent's
screenshots attached, and ours have none, so its own scores don't compare
with bubench-run's. This reads the steps and final answer it saved under
`run_data/` and judges them again without the screenshots, writing
scores.json in bubench-grade's shape next to upstream's own verdicts.

    cd .bu-bench && uv run python run_framework_eval.py --framework browser-use \\
        --browser local_headless --model gemini-3.8-flash --parallel 2 --task-timeout 1800
    uv run bubench-control-grade .bu-bench/run_data/<run> --out-dir results/bubench-control/<run>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..common import emit_scores, mean
from . import upstream
from .grade import STEPS_MAX_CHARS, add_judge_args


def fit_steps(steps: list[str]) -> list[str]:
    """Cut every step to one common length when the whole run doesn't fit the
    judge's window, as bubench-grade does for Lightpanda's steps. Otherwise
    the judge drops the end of a long run, where the answer is found."""

    def size(limit: int) -> int:
        return sum(min(len(s), limit) + 1 for s in steps)

    longest = max((len(s) for s in steps), default=0)
    if size(longest) <= STEPS_MAX_CHARS:
        return steps
    lo, hi = 0, longest
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if size(mid + 3) <= STEPS_MAX_CHARS:
            lo = mid
        else:
            hi = mid - 1
    return [s[:lo] + "..." if len(s) > lo else s for s in steps]


def load_run(run_dir: Path) -> list[dict[str, Any]]:
    """One artifact per task, skipping the `_task_results` summaries."""
    return [json.loads(p.read_text()) for p in sorted(run_dir.glob("*.json"))]


async def _judge_all(
    artifacts: list[dict[str, Any]],
    tasks: dict[str, dict[str, Any]],
    *,
    bu_bench_dir: Path,
    judge_model: str,
    concurrency: int,
) -> dict[str, dict[str, Any]]:
    sem = asyncio.Semaphore(concurrency)

    async def one(a: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        trace = a.get("agent_trace")
        if trace is None:
            return a["task_id"], {"verdict": False, "no_trace": True}
        async with sem:
            try:
                verdict = await upstream.judge(
                    bu_bench_dir,
                    task=tasks[a["task_id"]],
                    final_result=trace["final_result"],
                    agent_steps=fit_steps(trace["agent_steps"]),
                    judge_model=judge_model,
                )
            except Exception as e:
                print(f"judge error on {a['task_id']}: {type(e).__name__}: {e}", file=sys.stderr)
                return a["task_id"], {"judge_error": f"{type(e).__name__}: {e}"}
            return a["task_id"], verdict

    return dict(await asyncio.gather(*(one(a) for a in artifacts)))


def grade_run(
    run_dir: Path, *, bu_bench_dir: Path, judge_model: str, concurrency: int
) -> dict[str, Any]:
    artifacts = load_run(run_dir)
    tasks = {t["task_id"]: t for t in upstream.load_tasks(bu_bench_dir)}
    verdicts = asyncio.run(
        _judge_all(
            artifacts,
            tasks,
            bu_bench_dir=bu_bench_dir,
            judge_model=judge_model,
            concurrency=concurrency,
        )
    )

    per_task = []
    for a in artifacts:
        v = verdicts[a["task_id"]]
        metrics = a.get("metrics") or {}
        per_task.append(
            {
                "id": a["task_id"],
                "category": tasks[a["task_id"]]["category"],
                "score": None if "judge_error" in v else float(v["verdict"]),
                "upstream_score": float(a["score"]),
                "duration_s": metrics.get("duration"),
                "steps": metrics.get("steps"),
                "judgement": v,
            }
        )

    scored = [t for t in per_task if t["score"] is not None]
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in scored:
        by_category[t["category"]].append(t)
    return {
        "run_dir": str(run_dir),
        "bu_bench_commit": upstream.commit(bu_bench_dir),
        "judge_model": judge_model,
        "n_tasks": len(per_task),
        "n_scored": len(scored),
        "judge_errors": len(per_task) - len(scored),
        "accuracy": mean(t["score"] for t in scored),
        "upstream_accuracy": mean(t["upstream_score"] for t in per_task),
        "reached_captcha": sum(1 for t in scored if t["judgement"].get("reached_captcha")),
        "by_category": {
            c: {"n": len(ts), "accuracy": mean(t["score"] for t in ts)}
            for c, ts in sorted(by_category.items())
        },
        "avg_duration_s": mean(t["duration_s"] or 0 for t in per_task),
        "avg_steps": mean(t["steps"] or 0 for t in per_task),
        "per_task": per_task,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("run_dir", type=Path, help="Upstream run_data/<run> directory")
    parser.add_argument("--out-dir", type=Path, required=True)
    add_judge_args(parser)
    args = parser.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    scores = grade_run(
        args.run_dir,
        bu_bench_dir=args.bu_bench_dir.resolve(),
        judge_model=args.judge_model,
        concurrency=args.judge_concurrency,
    )
    emit_scores(scores, args.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
