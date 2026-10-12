"""The parts of browser-use/benchmark that BU Bench V1 scores depend on.

Tasks, their order and the judge come from a checkout of the upstream repo
(`--bu-bench-dir`), imported as-is rather than ported: a re-implemented
judge is the main reason self-reported BU Bench numbers don't compare.

    git clone https://github.com/browser-use/benchmark .bu-bench
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

BENCHMARK = "BU_Bench_V1"
# `frameworks._get_judge_llm`'s default, which every published V1 run used.
JUDGE_MODEL = "gemini-2.5-flash"


def _import_path(bu_bench_dir: Path) -> None:
    if not (bu_bench_dir / f"{BENCHMARK}.enc").exists():
        raise SystemExit(
            f"error: no {BENCHMARK}.enc in {bu_bench_dir}\n"
            "hint: git clone https://github.com/browser-use/benchmark " + str(bu_bench_dir)
        )
    if str(bu_bench_dir) not in sys.path:
        sys.path.insert(0, str(bu_bench_dir))


def load_tasks(bu_bench_dir: Path) -> list[dict[str, Any]]:
    """The 100 V1 tasks, decrypted in memory, in the order upstream's
    distributed runner uses (one task per category in turn), so `--limit 10`
    samples every category."""
    _import_path(bu_bench_dir)
    from frameworks import interleave
    from frameworks import load_tasks as upstream_load_tasks

    return interleave(upstream_load_tasks(BENCHMARK))


def commit(bu_bench_dir: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(bu_bench_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip()


async def judge(
    bu_bench_dir: Path,
    *,
    task: dict[str, Any],
    final_result: str,
    agent_steps: list[str],
    judge_model: str,
) -> dict[str, Any]:
    """Upstream's `run_and_judge` judging step, without its executor."""
    _import_path(bu_bench_dir)
    from browser_use import ChatGoogle
    from judge import JudgementResult, construct_judge_messages

    messages = construct_judge_messages(
        task=task["confirmed_task"],
        final_result=final_result,
        agent_steps=agent_steps,
        ground_truth=task.get("answer"),
        screenshots_b64=[],
    )
    llm = ChatGoogle(model=judge_model, api_key=os.getenv("GOOGLE_API_KEY"))
    response = await llm.ainvoke(messages, output_format=JudgementResult)
    return response.completion.model_dump()
