"""
Block answer-key lookups in the Claude + MCP runners, and flag them in grading.

With a browser, Claude sometimes looks up the benchmark's own answers: it
searches for the task id or "GAIA benchmark", or opens a copy of the
dataset or another agent's logged run on Hugging Face or GitHub. This module
holds one deny list for that, used two ways:

  - As a Claude Code `PreToolUse` hook (`python lookup_block.py --hook
    <config.json>`), passed per run by `_mcp.run_mcp_task` through
    `claude --settings`. It sees every MCP tool call on every backend, so it
    covers any tool that takes a URL, a query or a script (Lightpanda's
    `goto`, `search`, `markdown`/`tree`/`html` with a `url`, `eval`;
    agent-browser's `open`; browser-use's `browser_navigate`; pilotfish's
    `browser_navigate`, `browser_search`, `browser_get_text`), and denies the
    call with a reason Claude can read.
  - From the graders (`lookup_flags`), which mark each task whose trace
    tried a lookup, blocked or not, so a run can be scored with and without
    them.

The deny list names specific dataset and agent-trace repos, not whole hosts:
tasks can legitimately need Hugging Face or GitHub. Every entry below was
opened by an agent looking for an answer in a recorded run.

What a hook cannot see: a click on a link (its arguments carry an element
ref, not a URL), and an answer quoted in a search result's snippet. The
graders' flag shares that blind spot, so it is a lower bound.

This file only imports the standard library: the hook runs it as a script,
once per tool call.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus

# Marker at the start of every deny reason. Claude sees it as the tool's
# error; the runner's stream parser uses it to mark the call blocked.
BLOCK_MARKER = "[lookup-blocked]"

# Hugging Face and GitHub repos (owner/name, case-insensitive) that publish
# GAIA or AssistantBench questions with answers, or agents' logs of them.
# Matched as a whole path segment pair anywhere in a URL, query or script,
# so datasets-server URLs (`?dataset=owner/name`) and raw.githubusercontent
# files are covered too.
DENIED_REPOS: tuple[str, ...] = (
    # The benchmarks themselves.
    "gaia-benchmark/GAIA",
    "gaia-benchmark/leaderboard",
    "AssistantBench/AssistantBench",
    "oriyor/assistantbench",
    # Copies of GAIA's validation split.
    "cmriat/gaia",
    "bstraehle/validation",
    "lauspectrum/gaia-validation-sampled_50",
    "huggingface/GAIA",
    "api-ai/gaia",
    "assafmo/GAIA-benchmark-solutions",
    "zorazrw/ai4work-resources",
    "LINs-lab/MASArena",
    "paopao-13/pecs-multi-agent",
    "OpenHands/benchmarks",
    "harbor-framework/harbor-index",
    # Agent traces and failure-attribution sets built on GAIA/AssistantBench.
    "Kevin355/Who_and_When",
    "microsoft/AgentRx",
    "McGill-NLP/agent-reward-bench",
    "yuanyyaa/agent-reward-bench",
    "RobinChen2001/full_agent_traces",
    # GAIA agents and course Spaces that ship the questions with answers.
    "parkuman/gaia-agent",
    "gaia-agent/gaia-agent",
    "V1ktor2706/gaia-ai-agent",
    "jatinsihag2345/gaia-agent-eval-harness",
    "Swaticuh/gaia-final-pass",
    "romain-fayoux/Final_Assignment_Template",
)

# Hugging Face Spaces are also served from <owner>-<name>.hf.space. Hosts
# for repos that are not Spaces never resolve, so listing them is harmless.
_SPACE_HOSTS = tuple(re.sub(r"[^a-z0-9]+", "-", r.lower()) + ".hf.space" for r in DENIED_REPOS)

_REPO_RE = re.compile(
    r"(?<![\w.-])(?:" + "|".join(re.escape(r.lower()) for r in DENIED_REPOS) + r")(?![\w.-])"
)

# The benchmark named in a search or URL. "Gaia" in any case is also the ESA
# mission (its data releases, "GAIA DR3"), and "assistant bench" is
# furniture, so both only count next to a benchmark word. "AssistantBench"
# written as one word is only ever the benchmark.
_BENCH_WORDS = r"benchmarks?|leaderboard|validation|level\W{0,3}[123]|answers?"
_NAMES = r"gaia|assistant\W?bench"
_BENCHMARK_QUERY_RE = re.compile(
    r"\bassistant[-_]?bench\b"
    rf"|\b(?:{_NAMES})\W{{1,3}}(?:{_BENCH_WORDS})\b"
    rf"|\b(?:{_BENCH_WORDS})\W{{1,3}}(?:{_NAMES})\b",
    re.IGNORECASE,
)

# A local file. GAIA attachments are named <task_id>.<ext> and agents open
# them by path: the native runners hand over their own copy, and MCP runs
# guess file:///tmp/<task_id>.xlsx. That names the task without looking it
# up, so task ids are not checked in a local path.
_LOCAL_PATH_RE = re.compile(r"^\s*(?:file:|/|~[\\/]|\.{1,2}[\\/]|[a-z]:[\\/])", re.IGNORECASE)


def _strings(value: Any) -> Iterator[str]:
    """Every string inside a tool's arguments, at any depth."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def _decoded(text: str) -> str:
    """`text` with URL escapes undone (twice, for double-encoded search
    redirects), so `gaia-benchmark%2FGAIA` matches `gaia-benchmark/GAIA`."""
    return unquote_plus(unquote_plus(text))


def lookup_reason(args: Any, task_ids: Iterable[str] = ()) -> str | None:
    """Why a tool call with `args` is an answer-key lookup, or None.

    `task_ids` are the suite's own ids (GAIA UUIDs, AssistantBench hashes):
    they only appear in copies of the benchmark, and in the name of a task's
    own attachment, which is why local file paths are not checked for them."""
    ids = [t.lower() for t in task_ids if t]
    for raw in _strings(args):
        text = _decoded(raw)
        low = text.lower()
        m = _REPO_RE.search(low)
        if m:
            return f"a copy of the benchmark or of agents' runs on it ({m.group(0)})"
        host = next((h for h in _SPACE_HOSTS if h in low), None)
        if host:
            return f"a copy of the benchmark or of agents' runs on it ({host})"
        if not _LOCAL_PATH_RE.match(text):
            task_id = next((t for t in ids if t in low), None)
            if task_id:
                return f"the benchmark's task id ({task_id})"
        m = _BENCHMARK_QUERY_RE.search(text)
        if m:
            return f"the benchmark's name ({m.group(0)!r})"
    return None


# --- Claude Code hook ----------------------------------------------------

# Tools the hook is registered for: every MCP tool, plus the built-in web
# tools in case a run allows them.
HOOK_MATCHER = "mcp__.*|WebFetch|WebSearch"


def hook_settings(config_path: Path, python: str | None = None) -> dict[str, Any]:
    """The `claude --settings` JSON that registers the hook for one run."""
    python = python or sys.executable
    command = f'"{python}" "{Path(__file__).resolve()}" --hook "{config_path}"'
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": HOOK_MATCHER,
                    "hooks": [{"type": "command", "command": command, "timeout": 30}],
                }
            ]
        }
    }


def write_hook_config(path: Path, task_ids: Iterable[str]) -> Path:
    """Store the suite's task ids for the hook to read on every call."""
    path.write_text(json.dumps({"task_ids": sorted(set(task_ids))}, indent=0))
    return path


def run_hook(config_path: Path, stdin: str) -> str:
    """Handle one PreToolUse event: an empty string allows the call, a JSON
    decision denies it."""
    try:
        event = json.loads(stdin or "{}")
    except json.JSONDecodeError:
        return ""
    try:
        task_ids = json.loads(config_path.read_text()).get("task_ids", [])
    except (OSError, json.JSONDecodeError):
        task_ids = []
    reason = lookup_reason(event.get("tool_input"), task_ids)
    if reason is None:
        return ""
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"{BLOCK_MARKER} This call looks up {reason}, which this benchmark "
                    "run does not allow. Find the answer from primary sources instead."
                ),
            }
        }
    )


# --- Grading ---------------------------------------------------------------


def grading_task_ids(predictions_path: Path, run_ids: Iterable[str]) -> list[str]:
    """The task ids a grader checks traces for: the ids in the predictions
    file, plus the whole split's from the run's `lookup-block.json` when the
    runner wrote one, so a `--limit` or partial run still flags a search for
    a task it didn't run."""
    ids = {t for t in run_ids if t}
    config = predictions_path.parent / "lookup-block.json"
    with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
        ids.update(json.loads(config.read_text())["task_ids"])
    return sorted(ids)


def lookup_flags(trace: list[dict[str, Any]] | None, task_ids: Iterable[str]) -> dict[str, Any]:
    """Per-task lookup summary for a grader: every call that tried a lookup,
    whether the hook blocked it or (in a run without the hook) it went
    through. `blocked` counts calls the runner saw denied by the hook."""
    ids = list(task_ids)
    hits = []
    blocked = 0
    for call in trace or []:
        reason = lookup_reason(call.get("args"), ids)
        if call.get("blocked"):
            blocked += 1
            reason = reason or "blocked by the hook"
        if reason:
            hits.append(f"{call.get('tool')}: {reason}")
    return {"lookup_attempted": bool(hits), "lookup_blocked": blocked, "lookup_hits": hits[:5]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--hook", type=Path, required=True, help="Hook config JSON (task ids)")
    args = parser.parse_args(argv)
    out = run_hook(args.hook, sys.stdin.read())
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
