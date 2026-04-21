"""Shared utilities for benchmark runners and graders.

Each suite (assistantbench, gaia, ...) drives lightpanda one-shot and writes
JSONL predictions with the same envelope and status reporting. This module
factors out the common mechanics so suites can focus on their rubric.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable

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
    """Run a single task through lightpanda. Returns (prediction, duration_s, timed_out, stderr_tail, returncode)."""
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
