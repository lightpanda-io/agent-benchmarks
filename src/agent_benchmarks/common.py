"""Shared utilities for benchmark runners and graders.

Each suite (assistantbench, gaia, ...) drives lightpanda one-shot and writes
JSONL predictions with the same envelope and status reporting. This module
factors out the common mechanics so suites can focus on their rubric.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STDERR_TAIL_BYTES = 8 * 1024


def run_lightpanda_task(
    *,
    lightpanda: Path,
    provider: str,
    model: str | None,
    user_agent: str | None,
    system_prompt: str,
    task_prompt: str,
    attachment: Path | None = None,
    timeout_s: float,
) -> tuple[str, float, bool, str, int | None]:
    """Run a single task through lightpanda.

    Returns (prediction, duration_s, timed_out, stderr_tail, returncode).
    """
    cmd: list[str] = [str(lightpanda), "agent", "--provider", provider]
    if model:
        cmd += ["--model", model]
    if user_agent:
        cmd += ["--user-agent", user_agent]
    cmd += ["--system-prompt", system_prompt]
    cmd += ["--task", task_prompt]
    if attachment is not None:
        cmd += ["--task-attachment", str(attachment)]

    env = dict(os.environ)

    started = time.monotonic()
    timed_out = False
    returncode: int | None = None
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            env=env,
            check=False,
        )
        stdout = proc.stdout
        stderr = proc.stderr
        returncode = proc.returncode
    except subprocess.TimeoutExpired as e:
        timed_out = True
        stdout = (
            (e.stdout or b"").decode("utf-8", errors="replace")
            if isinstance(e.stdout, bytes)
            else (e.stdout or "")
        )
        stderr = (
            (e.stderr or b"").decode("utf-8", errors="replace")
            if isinstance(e.stderr, bytes)
            else (e.stderr or "")
        )

    duration_s = time.monotonic() - started

    if len(stderr) > STDERR_TAIL_BYTES:
        stderr_tail = "...[truncated]...\n" + stderr[-STDERR_TAIL_BYTES:]
    else:
        stderr_tail = stderr

    prediction = stdout.strip()
    return prediction, duration_s, timed_out, stderr_tail, returncode


def load_completed_ids(predictions_path: Path) -> set[str]:
    """Read a predictions.jsonl and return the set of already-completed ids.

    Returns an empty set if the file doesn't exist yet (first run, no resume).
    """
    completed: set[str] = set()
    try:
        f = predictions_path.open()
    except FileNotFoundError:
        return completed
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("id"):
                completed.add(row["id"])
    return completed


def resolve_lightpanda_binary(lightpanda_arg: Path, project_root: Path) -> Path:
    """Resolve the lightpanda binary path. Absolute paths pass through; relative
    paths are tried against CWD then <project_root>/../. Caller must check
    .exists() and report the error — we always return a resolved path so the
    error message can point at a concrete location."""
    if lightpanda_arg.is_absolute():
        return lightpanda_arg.resolve()
    candidates = [Path.cwd() / lightpanda_arg, project_root.parent / lightpanda_arg]
    return next((c for c in candidates if c.exists()), candidates[0]).resolve()


def print_lightpanda_missing(resolved: Path) -> None:
    print(f"error: lightpanda binary not found at {resolved}", file=sys.stderr)
    print("hint: build first with `zig build -Doptimize=ReleaseFast`", file=sys.stderr)


def status_label(result: dict[str, Any]) -> str:
    if result["timed_out"]:
        return "TIMEOUT"
    return "OK" if result["prediction"] else "EMPTY"


def mean(xs: Iterable[float]) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def add_common_runner_args(parser: argparse.ArgumentParser, *, suite_name: str) -> None:
    """Register the flags shared by every benchmark runner. Suite-specific
    flags (e.g. --level for GAIA) should be added separately by the caller."""
    parser.add_argument("--split", default="validation", choices=["validation", "test"])
    parser.add_argument("--limit", type=int, default=None, help="Run at most N tasks")
    parser.add_argument("--workers", type=int, default=1, help="Parallel lightpanda subprocesses")
    parser.add_argument("--timeout", type=float, default=300.0, help="Per-task timeout in seconds")
    parser.add_argument(
        "--lightpanda",
        type=Path,
        default=Path("zig-out/bin/lightpanda"),
        help=(
            "Path to the lightpanda binary. If relative, tries CWD then "
            "<pyproject>/../ (default: zig-out/bin/lightpanda)"
        ),
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
        help=f"Output dir (default: results/{suite_name}/<timestamp>/)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip task ids already present in <out-dir>/predictions.jsonl",
    )


def resolve_out_dir(out_dir_arg: Path | None, project_root: Path, suite_name: str) -> Path:
    """Pick --out-dir if given, otherwise results/<suite>/<UTC-timestamp>/.
    Creates the directory and returns it."""
    if out_dir_arg is not None:
        out_dir = out_dir_arg
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = project_root / "results" / suite_name / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def run_benchmark_tasks(
    pending: list[dict[str, Any]],
    work_fn: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    predictions_path: Path,
    workers: int,
    timeout_s: float,
    provider: str,
    model: str | None,
    preview_fn: Callable[[dict[str, Any]], str] | None = None,
) -> None:
    """Execute work_fn over pending rows, appending each result to predictions_path
    as JSONL and printing a progress line per completion to stderr.

    workers <= 1 runs serially; otherwise a ThreadPoolExecutor drives it.
    preview_fn(row) returns an optional short task description appended to the line.
    """
    total = len(pending)
    print(
        f"Running {total} task(s) with {workers} worker(s), "
        f"timeout {timeout_s:.0f}s, provider={provider}" + (f", model={model}" if model else ""),
        file=sys.stderr,
    )
    print(f"Output dir: {predictions_path.parent}", file=sys.stderr)

    with predictions_path.open("a") as preds:

        def _emit(idx: int, row: dict[str, Any], result: dict[str, Any]) -> None:
            preds.write(json.dumps(result) + "\n")
            preds.flush()
            tail = f" — {preview_fn(row)[:80]}" if preview_fn else ""
            print(
                f"[{idx}/{total}] {status_label(result)} "
                f"{result['duration_s']:.1f}s {result['id'][:12]}{tail}",
                file=sys.stderr,
            )

        if workers <= 1:
            for idx, row in enumerate(pending, 1):
                _emit(idx, row, work_fn(row))
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {ex.submit(work_fn, row): row for row in pending}
                for done, fut in enumerate(concurrent.futures.as_completed(futures), 1):
                    _emit(done, futures[fut], fut.result())


def emit_scores(scores: dict[str, Any], out_dir: Path) -> None:
    """Write scores.json, print the summary (everything except per_task) to
    stdout, and print the results path to stderr."""
    (out_dir / "scores.json").write_text(json.dumps(scores, indent=2))
    summary = {k: v for k, v in scores.items() if k != "per_task"}
    print(json.dumps(summary, indent=2))
    print(f"\nResults: {out_dir}", file=sys.stderr)
