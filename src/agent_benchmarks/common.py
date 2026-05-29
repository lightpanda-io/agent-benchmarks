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
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STDERR_TAIL_BYTES = 8 * 1024

# <ANSWER>...</ANSWER> envelope. Prompts that instruct the agent to wrap its
# final answer in these tags let us extract the canonical answer separate
# from any reasoning prose. Multi-line answers (lists, JSON dicts) are
# allowed inside, hence re.DOTALL. If the agent emits multiple envelopes
# (e.g. demonstrating format inside its reasoning), prefer the LAST one —
# that's what models tend to use for the final answer.
ANSWER_RE = re.compile(r"<ANSWER>(.*?)</ANSWER>", re.DOTALL | re.IGNORECASE)


def extract_answer_envelope(prediction: str) -> tuple[str, str | None]:
    """If `prediction` contains one or more <ANSWER>...</ANSWER> blocks,
    return the last one (stripped). Otherwise return the raw prediction
    with a note so the caller can record that the envelope was missing —
    useful for diagnosing format non-compliance without losing the partial
    signal.
    """
    if not prediction:
        return prediction, None
    matches = ANSWER_RE.findall(prediction)
    if not matches:
        return prediction, "no <ANSWER> envelope found; using raw result"
    return matches[-1].strip(), None

# Cap each Lightpanda subprocess's memory + swap via a systemd-run user-scope
# cgroup, when systemd-run is available. Lightpanda has a known regression on
# some JS-heavy pages (GitHub Copilot marketing, etc.) where RSS balloons to
# 14+ GiB and can OOM the host. A cgroup RSS cap kills a runaway cleanly
# (cgroup OOM killer, returncode=-9) without touching any sibling processes.
#
# Virtual-memory rlimits (RLIMIT_AS) don't work: Lightpanda reserves large
# virtual regions at init for V8/arenas, so any cap tight enough to catch a
# runaway also kills healthy processes on startup. cgroup MemoryMax tracks
# committed RSS + swap, which is what we actually care about.
#
# Default cap: 6000 MB (6 GiB). Healthy tasks use <500 MB RSS, so this is
# ~12x headroom. Tune with LIGHTPANDA_MEMORY_MAX_MB (0 or empty = disable).
_SYSTEMD_RUN = shutil.which("systemd-run")
_DEFAULT_MEMORY_MAX_MB = 6000


def _memory_max_mb() -> int:
    raw = os.environ.get("LIGHTPANDA_MEMORY_MAX_MB")
    if raw is None:
        return _DEFAULT_MEMORY_MAX_MB
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_MEMORY_MAX_MB


def _wrap_with_cgroup_cap(cmd: list[str]) -> list[str]:
    """Prepend a systemd-run --user --scope wrapper that caps memory + swap.
    Returns `cmd` unchanged when systemd-run is unavailable or the cap is
    disabled (env LIGHTPANDA_MEMORY_MAX_MB=0)."""
    if _SYSTEMD_RUN is None:
        return cmd
    mb = _memory_max_mb()
    if mb <= 0:
        return cmd
    return [
        _SYSTEMD_RUN,
        "--user",
        "--scope",
        "--quiet",
        "-p",
        f"MemoryMax={mb}M",
        "-p",
        "MemorySwapMax=0",
        "--",
        *cmd,
    ]


# ANSI SGR escape sequences (colors, styles) that wrap Terminal.printToolCall
# output in the Zig agent. Stripped before extracting tool-call lines.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# The agent logs each tool call and its result as `[tool: <name>] <json_args>`
# and `[result: <name>] <content>` (Terminal.zig :printToolCall, :printToolResult).
# Tool-call args are single-line JSON; result bodies can span newlines (markdown
# dumps, DOM trees). This regex captures either kind and reads body text up to
# the next marker or end of string.
#
# Note: Terminal.printToolResult truncates to 500 chars with a "..." suffix
# before writing to stderr, so `output` text here is a preview, not the full
# tool response the agent's LLM received.
_TOOL_ENTRY_RE = re.compile(
    r"\[(tool|result): (\S+)\] (.*?)(?=\n\[(?:tool|result): |\Z)",
    re.DOTALL,
)

# Tools whose outputs describe what the agent "saw" on a page — i.e. textual
# substitutes for a screenshot. Order here matters only for documentation; the
# judge decides which ones are useful.
PAGE_SNAPSHOT_TOOLS = frozenset(
    {"markdown", "extract", "tree", "interactiveElements", "structuredData"}
)


def parse_tool_trace(stderr: str) -> list[dict[str, Any]]:
    """Extract the agent's tool-call timeline from its stderr stream.

    Each entry is `{"tool": <name>, "args": <parsed-json>, "output": <str>?}`.
    `output` is the tool's result body as printed to stderr — present when the
    call had a matching `[result: <name>] ...` entry immediately after it in
    the log. Calls whose args aren't parseable JSON are dropped. Order is
    preserved so callers can reconstruct the navigation timeline.
    """
    stripped = _ANSI_RE.sub("", stderr)
    items: list[tuple[str, str, str]] = [
        (m.group(1), m.group(2), m.group(3).strip()) for m in _TOOL_ENTRY_RE.finditer(stripped)
    ]

    trace: list[dict[str, Any]] = []
    i = 0
    while i < len(items):
        kind, name, body = items[i]
        if kind != "tool":
            # A result with no preceding tool call; skip rather than invent a
            # parent, which would misalign later pairings.
            i += 1
            continue
        try:
            args = json.loads(body)
        except json.JSONDecodeError:
            i += 1
            continue
        entry: dict[str, Any] = {"tool": name, "args": args}
        # Attach an immediately-following result for the same tool. The agent
        # never interleaves calls with unrelated results, so a name mismatch
        # means the expected result was lost (e.g. off the stderr tail).
        if i + 1 < len(items):
            next_kind, next_name, next_body = items[i + 1]
            if next_kind == "result" and next_name == name:
                entry["output"] = next_body
                i += 2
                trace.append(entry)
                continue
        trace.append(entry)
        i += 1
    return trace


def parse_lightpanda_usage(stderr: str) -> dict[str, Any] | None:
    """Pull the `$usage prompt=… completion=… …` summary line that
    `lightpanda agent --task` writes to stderr at end of one-shot mode.
    Returns the parsed usage dict or None if the line is absent (older
    binaries, or runs that crashed before printing it).
    """
    if not stderr or "$usage " not in stderr:
        return None
    # Scan from the bottom so a partial echo earlier in stderr doesn't win.
    for line in reversed(stderr.splitlines()):
        line = line.strip()
        if not line.startswith("$usage "):
            continue
        out: dict[str, Any] = {}
        for kv in line[len("$usage "):].split():
            if "=" not in kv:
                continue
            k, v = kv.split("=", 1)
            try:
                out[k] = int(v)
            except ValueError:
                continue
        if not out:
            return None
        # Normalize to the same shape the MCP path emits (input_tokens etc.)
        # so downstream summarize_usage and the progress-line formatter
        # handle both paths uniformly.
        return {
            "input_tokens": out.get("prompt", 0),
            "output_tokens": out.get("completion", 0),
            "cache_read_input_tokens": out.get("cached", 0),
            "cache_creation_input_tokens": out.get("cache_creation", 0),
            "num_turns": 0,  # native agent doesn't expose per-turn count
            "final_turn_input_tokens": 0,  # likewise
            "total_cost_usd": None,  # native path doesn't compute this itself
        }
    return None


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
) -> tuple[str, float, bool, str, int | None, list[dict[str, Any]], dict[str, Any] | None]:
    """Run a single task through lightpanda.

    Returns (prediction, duration_s, timed_out, stderr_tail, returncode, trace, usage).
    `trace` is the parsed tool-call list from the full stderr stream, captured
    before the tail-truncation step — so it survives long runs where the
    first tool calls would otherwise roll off the 8 KiB stderr tail.
    `usage` is the parsed `$usage` summary line emitted by recent agent
    builds; None if the binary didn't emit one.
    """
    cmd: list[str] = [str(lightpanda), "agent", "--provider", provider]
    if model:
        cmd += ["--model", model]
    if user_agent:
        cmd += ["--user-agent", user_agent]
    cmd += ["--system-prompt", system_prompt]
    cmd += ["--task", task_prompt]
    if attachment is not None:
        cmd += ["--attach", str(attachment)]

    cmd = _wrap_with_cgroup_cap(cmd)

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

    # Parse trace from the FULL stderr before we truncate — otherwise long
    # runs lose their early tool calls along with the head of stderr.
    trace = parse_tool_trace(stderr)
    usage = parse_lightpanda_usage(stderr)

    if len(stderr) > STDERR_TAIL_BYTES:
        stderr_tail = "...[truncated]...\n" + stderr[-STDERR_TAIL_BYTES:]
    else:
        stderr_tail = stderr

    prediction = stdout.strip()
    return prediction, duration_s, timed_out, stderr_tail, returncode, trace, usage


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
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out_dir = project_root / "results" / suite_name / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def summarize_usage(tasks: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Roll a list of predictions.jsonl rows into avg/total token+cost stats.

    Reads `row["usage"]` (the schema produced by `_parse_claude_stream` in
    `_mcp.py`). Returns a dict ready to splice into scores.json:

      {
        "n_with_usage": <int>,
        "avg_input_tokens": <non-cached input, summed across turns>,
        "avg_cache_read_input_tokens": <cached/replayed context>,
        "avg_cache_creation_input_tokens": <new cache writes>,
        "avg_output_tokens": <model output>,
        "avg_num_turns": <assistant API calls per task>,
        "avg_final_turn_input_tokens": <peak context window per task>,
        "avg_total_cost_usd": <per task>,
        "total_cost_usd": <sum across tasks>,
      }

    Tasks without usage (older runs, gemini-driven runs) are skipped — the
    `n_with_usage` count tells you how many contributed.
    Returns `{"n_with_usage": 0}` if nothing usable is found.
    """
    rows: list[dict[str, Any]] = []
    for t in tasks:
        u = t.get("usage")
        if isinstance(u, dict):
            rows.append(u)
    if not rows:
        return {"n_with_usage": 0}

    def _avg(key: str) -> float:
        vals = [r.get(key) or 0 for r in rows]
        return sum(vals) / len(rows)

    costs = [r.get("total_cost_usd") for r in rows]
    cost_vals = [c for c in costs if isinstance(c, (int, float))]
    return {
        "n_with_usage": len(rows),
        "avg_input_tokens": _avg("input_tokens"),
        "avg_cache_read_input_tokens": _avg("cache_read_input_tokens"),
        "avg_cache_creation_input_tokens": _avg("cache_creation_input_tokens"),
        "avg_output_tokens": _avg("output_tokens"),
        "avg_num_turns": _avg("num_turns"),
        "avg_final_turn_input_tokens": _avg("final_turn_input_tokens"),
        "avg_total_cost_usd": (sum(cost_vals) / len(cost_vals)) if cost_vals else None,
        "total_cost_usd": sum(cost_vals) if cost_vals else None,
    }


def _format_usage_summary(usage: dict[str, Any] | None) -> str:
    """One-line `in/out/ctx tok, $cost ` for the per-task progress line.

    `in`  = total non-cached input tokens summed across turns
    `out` = total output tokens
    `ctx` = peak context window = final turn's (input + cache_read + cache_creation),
            i.e. how full the window got at the end of the conversation
    """
    if not usage:
        return ""

    def _k(v: float | int) -> str:
        if v >= 1000:
            return f"{v/1000:.1f}k"
        return str(int(v))

    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)
    ctx = usage.get("final_turn_input_tokens", 0)
    cost = usage.get("total_cost_usd")
    cost_str = f", ${cost:.3f}" if isinstance(cost, (int, float)) else ""
    return f"in={_k(inp)} out={_k(out)} ctx={_k(ctx)}{cost_str} "


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
            usage_str = _format_usage_summary(result.get("usage"))
            print(
                f"[{idx}/{total}] {status_label(result)} "
                f"{result['duration_s']:.1f}s {usage_str}{result['id'][:12]}{tail}",
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


# Run provenance: stamped next to predictions.jsonl at run start so that
# standalone re-grading (e.g. `webbench-grade <predictions> --judge-model X`)
# can carry the agent provider/model into scores.json without re-deriving
# it from CLI args. agent_model=None means the provider's baked-in default
# was used. `argv` is the full launch line — the canonical "how to
# reproduce this run" record (resume the same out-dir + same flags).
def write_run_manifest(out_dir: Path, *, agent_provider: str, agent_model: str | None) -> None:
    manifest = {
        "agent_provider": agent_provider,
        "agent_model": agent_model,
        "argv": sys.argv,
        "started_at": datetime.now(UTC).isoformat(),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))


def read_run_manifest(predictions_path: Path) -> dict[str, Any]:
    """Empty dict for runs that predate manifest stamping or for hand-curated
    predictions files — graders should always merge defensively."""
    p = predictions_path.parent / "manifest.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text())
