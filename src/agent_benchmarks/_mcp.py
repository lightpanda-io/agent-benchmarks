"""Subprocess helper for driving a benchmark via Claude Code + an MCP browser.

Same contract as `common.run_lightpanda_task` and
`_agent_browser.run_agent_browser_task`: one call per benchmark row, returns
(prediction, duration_s, timed_out, stderr_tail, returncode).

Architecture:

    task ──→ claude -p (LLM = Claude Sonnet/Opus/Haiku)
                │  stdio MCP
                ▼
          one browser MCP server
          (lightpanda mcp │ agent-browser-mcp)

The LLM is held constant across backends so the only thing varying between
runs is the browser engine + its MCP tool surface — the experiment we
actually want for cross-framework comparison.

The Lightpanda runners (`assistantbench-run`, `gaia-run`) are untouched.
This helper is additive.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# <ANSWER>...</ANSWER> envelope. The system prompt tells Claude to wrap its
# final answer in these tags so we can extract the canonical answer separate
# from any reasoning prose. Multi-line answers (e.g. AssistantBench lists,
# JSON dicts) are allowed inside, hence re.DOTALL. If Claude emits multiple
# envelopes (e.g. demonstrating format inside its reasoning), prefer the
# LAST one — that's what models tend to use for the final answer.
_ANSWER_RE = re.compile(r"<ANSWER>(.*?)</ANSWER>", re.DOTALL | re.IGNORECASE)

STDERR_TAIL_BYTES = 8 * 1024

# Where the agent-browser MCP server module lives. Relative to this file:
# benchmarks/src/agent_benchmarks/_mcp.py
#   → benchmarks/competitors/agent-browser-mcp/server.py
_AGENT_BROWSER_MCP_SERVER = (
    Path(__file__).resolve().parents[2] / "competitors" / "agent-browser-mcp" / "server.py"
)

# MCP tool allow-lists per backend. Naming follows Claude Code's convention:
# `mcp__<server-name>__<tool-name>`, where <server-name> is the key inside the
# `mcpServers` JSON dict the runner writes. Listing each tool explicitly is
# more robust than relying on a wildcard match in --allowed-tools, and
# documents which surface the benchmark gives the agent.
LIGHTPANDA_TOOLS: tuple[str, ...] = (
    "goto",
    "tree",
    "markdown",
    "extract",
    "structuredData",
    "findElement",
    "interactiveElements",
    "links",
    "search",
    "click",
    "fill",
    "hover",
    "selectOption",
    "setChecked",
    "press",
    "scroll",
    "waitForSelector",
    "nodeDetails",
    "getCookies",
    "getEnv",
    "getUrl",
    "eval",
    "consoleLogs",
    "detectForms",
)

AGENT_BROWSER_TOOLS: tuple[str, ...] = (
    "open",
    "snapshot",
    "click",
    "fill",
    "press",
    "find",
    "wait",
    "get",
    "eval",
    "back",
    "forward",
    "reload",
    "close",
)

# MCP server-name keys. These show up in `mcp__<key>__<tool>` allow-list
# entries, so keep them stable.
SERVER_NAME = {
    "lightpanda": "lightpanda",
    "agent-browser": "agent-browser",
}


def resolve_claude_bin(arg: str | None) -> str:
    if arg:
        return arg
    return os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"


def resolve_lightpanda_bin(arg: Path | None, project_root: Path) -> Path:
    """Pick the lightpanda binary path for the Lightpanda MCP backend.

    Mirrors common.resolve_lightpanda_binary: absolute paths pass through;
    relative tries CWD then <project_root>/../ (the typical
    benchmarks/-and-browser/-as-siblings layout)."""
    if arg is None:
        arg = Path("zig-out/bin/lightpanda")
    if arg.is_absolute():
        return arg.resolve()
    candidates = [Path.cwd() / arg, project_root.parent / arg]
    return next((c for c in candidates if c.exists()), candidates[0]).resolve()


def resolve_agent_browser_bin(arg: str | None) -> str:
    if arg:
        return arg
    return (
        os.environ.get("AGENT_BROWSER_BIN") or shutil.which("agent-browser") or "agent-browser"
    )


def ensure_executable(path: str | Path) -> bool:
    s = str(path)
    if "/" in s or "\\" in s:
        return Path(s).is_file() and os.access(s, os.X_OK)
    return shutil.which(s) is not None


def build_mcp_config(
    backend: str,
    *,
    lightpanda_bin: str | Path | None,
    agent_browser_bin: str | None,
    session: str,
    python_bin: str | None = None,
) -> dict[str, Any]:
    """Produce the JSON config Claude Code expects via --mcp-config.

    `session` is per-worker — passed into the MCP server's env so parallel
    workers don't share a Chrome (agent-browser) or trip over each other's
    Lightpanda state.
    """
    if backend == "lightpanda":
        if lightpanda_bin is None:
            raise ValueError("lightpanda_bin is required for the lightpanda backend")
        return {
            "mcpServers": {
                SERVER_NAME["lightpanda"]: {
                    "type": "stdio",
                    "command": str(lightpanda_bin),
                    "args": ["mcp"],
                    "env": {},
                },
            }
        }
    if backend == "agent-browser":
        if agent_browser_bin is None:
            raise ValueError("agent_browser_bin is required for the agent-browser backend")
        python = python_bin or sys.executable
        return {
            "mcpServers": {
                SERVER_NAME["agent-browser"]: {
                    "type": "stdio",
                    "command": python,
                    "args": [str(_AGENT_BROWSER_MCP_SERVER)],
                    "env": {
                        "AGENT_BROWSER_BIN": agent_browser_bin,
                        "AGENT_BROWSER_SESSION": session,
                    },
                }
            }
        }
    raise ValueError(f"unknown backend: {backend}")


def allowed_tools_for(backend: str) -> str:
    """Comma-separated mcp__<server>__<tool> list. Used with --allowed-tools
    to skip permission prompts for the MCP surface.

    NOTE: --allowed-tools in `claude -p` mode does NOT lock the agent down
    to only these tools — it's an allow-list for permission prompts,
    not a hard restriction. The real lockdown is done via
    `disallowed_builtin_tools()` + --disallowed-tools, see below.
    """
    if backend == "lightpanda":
        server = SERVER_NAME["lightpanda"]
        tools = LIGHTPANDA_TOOLS
    elif backend == "agent-browser":
        server = SERVER_NAME["agent-browser"]
        tools = AGENT_BROWSER_TOOLS
    else:
        raise ValueError(f"unknown backend: {backend}")
    return ",".join(f"mcp__{server}__{t}" for t in tools)


# Full list of built-in Claude Code tools, observed from the `system/init`
# event's "tools" field. We deny every non-MCP tool here so Claude can only
# act through the browser MCP. ToolSearch is the deferred-schema loader for
# MCP tools and stays allowed — without it, Claude can't materialize the
# input schemas for our browser tools and the run is dead in the water.
#
# An earlier version of this runner relied on --allowed-tools as a positive
# allow-list, which it isn't in -p mode: a 33-task Lightpanda run finished
# with 67× WebSearch, 48× WebFetch, 14× Bash, 11× Read, 11× Grep, 1× Agent
# all alongside the MCP calls. The browser comparison was contaminated.
# --disallowed-tools is the real lockdown.
DISALLOWED_BUILTIN_TOOLS: tuple[str, ...] = (
    "Bash",
    "BashOutput",
    "Edit",
    "Write",
    "NotebookEdit",
    "Read",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "Task",
    "Agent",
    "AskUserQuestion",
    "Skill",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskOutput",
    "TaskStop",
    "TaskUpdate",
    "ScheduleWakeup",
    "PushNotification",
    "Monitor",
    "EnterPlanMode",
    "ExitPlanMode",
    "EnterWorktree",
    "ExitWorktree",
    "CronCreate",
    "CronDelete",
    "CronList",
    "ListMcpResourcesTool",
    "ReadMcpResourceTool",
    # ToolSearch is intentionally NOT in this list — Claude Code uses it to
    # fetch deferred MCP tool schemas, so denying it would break MCP usage.
    # ToolSearch only loads schemas; it can't bypass --disallowed-tools to
    # actually invoke a denied tool.
)


def disallowed_builtin_tools() -> str:
    return ",".join(DISALLOWED_BUILTIN_TOOLS)


def make_session_pool(workers: int, prefix: str) -> queue.Queue[str]:
    """One stable session-name per worker, reused across tasks so a worker
    keeps its MCP-server subprocess (and hence its browser) warm."""
    q: queue.Queue[str] = queue.Queue()
    for i in range(max(1, workers)):
        q.put(f"{prefix}-w{i}")
    return q


def drain_pool(pool: queue.Queue[str]) -> list[str]:
    drained: list[str] = []
    while True:
        try:
            drained.append(pool.get_nowait())
        except queue.Empty:
            return drained


def close_agent_browser_sessions(binary: str, sessions: list[str]) -> None:
    """Best-effort `agent-browser --session <name> close` per worker after a
    benchmark run. Only relevant for the agent-browser backend — Lightpanda
    has no persistent process per session."""
    for name in sessions:
        with contextlib.suppress(subprocess.SubprocessError, OSError):
            subprocess.run(
                [binary, "--session", name, "close"],
                capture_output=True,
                timeout=10,
                check=False,
            )


def run_mcp_task(
    *,
    claude_bin: str,
    backend: str,
    mcp_config: dict[str, Any],
    allowed_tools: str,
    system_prompt: str,
    task_prompt: str,
    model: str | None,
    timeout_s: float,
) -> tuple[str, float, bool, str, int | None, list[dict[str, Any]]]:
    """Run one benchmark task through `claude -p` + the chosen MCP backend.

    Returns (prediction, duration_s, timed_out, stderr_tail, returncode, trace).
    `prediction` is the final `result` event of claude's stream-json output.
    `trace` is a list of `{tool, args}` entries — one per tool call Claude
    made, in order. Used to audit that Claude only hit MCP tools, not any
    built-in Claude Code tool.
    On error or empty output, the prediction is an empty string and the
    error is surfaced via stderr_tail.
    """
    # stream-json gives us per-event log instead of the single-blob `json`
    # mode, so we can collect tool_use blocks for the trace AND see in the
    # final `result` event whether any tool calls were denied (i.e. Claude
    # tried to use a built-in tool). --verbose is REQUIRED with
    # --print + --output-format=stream-json.
    cmd: list[str] = [
        claude_bin,
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--strict-mcp-config",
        "--mcp-config",
        json.dumps(mcp_config),
        # Skip permission prompts for the MCP tools we expect Claude to use.
        "--allowed-tools",
        allowed_tools,
        # Hard-deny every non-MCP built-in tool so the browser MCP is the
        # ONLY way Claude can do work. See DISALLOWED_BUILTIN_TOOLS for why
        # this is load-bearing (not --allowed-tools).
        "--disallowed-tools",
        disallowed_builtin_tools(),
        "--system-prompt",
        system_prompt,
    ]
    if model:
        cmd += ["--model", model]
    # Use stdin for the task to avoid argv-length surprises on long prompts
    # (GAIA attachments can run to tens of KB) and to keep the task body out
    # of `ps`/process listings.

    # When ANTHROPIC_API_KEY is set in the parent env, `claude -p` uses it
    # instead of the keychain OAuth token — which means subprocess invocations
    # bill against a possibly-exhausted API key rather than the user's Max
    # subscription. Strip it from the subprocess env so claude falls back to
    # ~/.claude/.credentials.json (the Max OAuth token). We learned this the
    # hard way: an earlier "clean" run returned `"Credit balance is too low"`
    # as the prediction on tasks where the API key billing hit its cap.
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    started = time.monotonic()
    timed_out = False
    returncode: int | None = None
    try:
        proc = subprocess.run(
            cmd,
            input=task_prompt,
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

    prediction, trace, parse_note = _parse_claude_stream(stdout)
    prediction, envelope_note = _extract_answer_envelope(prediction)
    note = "; ".join(n for n in (parse_note, envelope_note) if n)
    if note:
        # Splice the parse-time message into stderr so it lands in the
        # predictions row for debugging.
        stderr = (stderr or "") + f"\n[claude --print parse]\n{note}\n"

    if len(stderr) > STDERR_TAIL_BYTES:
        stderr_tail = "...[truncated]...\n" + stderr[-STDERR_TAIL_BYTES:]
    else:
        stderr_tail = stderr

    return prediction, duration_s, timed_out, stderr_tail, returncode, trace


def _parse_claude_stream(stdout: str) -> tuple[str, list[dict[str, Any]], str | None]:
    """Parse `claude --print --output-format=stream-json --verbose` output.

    The stream is one JSON object per line. We care about three event shapes:

      {"type":"system","subtype":"init","tools":[...],"mcp_servers":[...]}
        – sanity check that the right MCP is loaded.

      {"type":"assistant","message":{"content":[
          {"type":"text","text":"..."},
          {"type":"thinking","thinking":"..."},
          {"type":"tool_use","id":"...","name":"mcp__lightpanda__goto",
           "input":{"url":"..."}}
      ]}}
        – tool calls live in content blocks of type "tool_use".

      {"type":"result","subtype":"success","is_error":false,
       "result":"<final text>","permission_denials":[...]}
        – final answer. permission_denials lists any tool calls Claude
          attempted that the allow-list blocked; non-empty means leakage
          would have happened without --allowed-tools.

    Returns (prediction, trace, note). `trace` is a list of
    `{"tool": name, "args": parsed_input}` entries in call order. We do NOT
    store tool_result payloads — they're huge (snapshots) and the audit
    purpose only needs the call sites.
    """
    stripped = stdout.strip()
    if not stripped:
        return "", [], "empty stdout from claude --print"

    trace: list[dict[str, Any]] = []
    prediction = ""
    note_parts: list[str] = []
    saw_result = False

    for ln_no, ln in enumerate(stripped.splitlines(), 1):
        ln = ln.strip()
        if not ln:
            continue
        try:
            evt = json.loads(ln)
        except json.JSONDecodeError as e:
            note_parts.append(f"line {ln_no} not JSON ({e})")
            continue
        if not isinstance(evt, dict):
            continue

        etype = evt.get("type")
        if etype == "assistant":
            content = (evt.get("message") or {}).get("content") or []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    trace.append(
                        {
                            "tool": block.get("name", "?"),
                            "args": block.get("input", {}),
                        }
                    )
        elif etype == "result":
            saw_result = True
            denials = evt.get("permission_denials") or []
            if denials:
                # Each denial: {"tool_name":"...","tool_use_id":"...","tool_input":{...}}
                names = [d.get("tool_name", "?") for d in denials if isinstance(d, dict)]
                note_parts.append(
                    f"permission_denials ({len(denials)}): {','.join(names)}"
                )
            result = evt.get("result", "")
            if evt.get("is_error"):
                note_parts.append(f"claude is_error=true: {str(result)[:200]!r}")
            if isinstance(result, str):
                prediction = result.strip()
            else:
                note_parts.append(f"non-string `result` field: {type(result).__name__}")
        # else: system, user (tool_result), status events — skipped

    if not saw_result:
        note_parts.append("no terminal `result` event in stream")

    note = "; ".join(note_parts) if note_parts else None
    return prediction, trace, note


def _extract_answer_envelope(prediction: str) -> tuple[str, str | None]:
    """If `prediction` contains one or more <ANSWER>...</ANSWER> blocks,
    return the last one (stripped). Otherwise return the raw prediction
    with a note so the caller can see in stderr_tail that the envelope was
    missing — useful for diagnosing format non-compliance without losing
    the partial signal.
    """
    if not prediction:
        return prediction, None
    matches = _ANSWER_RE.findall(prediction)
    if not matches:
        return prediction, "no <ANSWER> envelope found; using raw result"
    return matches[-1].strip(), None


def add_common_mcp_args(parser: Any) -> None:
    """Shared argparse flags for an MCP-backed suite runner."""
    parser.add_argument(
        "--backend",
        choices=["lightpanda", "agent-browser"],
        required=True,
        help="Which browser MCP to drive Claude with.",
    )
    parser.add_argument(
        "--claude",
        default=None,
        help="Path to the claude CLI binary (default: $CLAUDE_BIN or PATH lookup)",
    )
    parser.add_argument(
        "--model",
        default="sonnet",
        help="Claude model alias or id (default: sonnet). e.g. sonnet, opus, haiku, "
        "claude-sonnet-4-6, claude-opus-4-7.",
    )
    parser.add_argument(
        "--lightpanda",
        type=Path,
        default=None,
        help="Path to the lightpanda binary (required for --backend lightpanda). "
        "Default: zig-out/bin/lightpanda resolved against CWD then <pyproject>/../.",
    )
    parser.add_argument(
        "--agent-browser",
        default=None,
        help="Path to the agent-browser binary (required for --backend agent-browser). "
        "Default: $AGENT_BROWSER_BIN or PATH lookup.",
    )
    parser.add_argument(
        "--split",
        default="validation",
        choices=["validation", "test"],
    )
    parser.add_argument("--limit", type=int, default=None, help="Run at most N tasks")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel `claude -p` subprocesses (each gets its own MCP server + browser)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="Per-task subprocess timeout in seconds. Claude Code's MCP tool calls "
        "can add a few seconds of overhead each, so this is higher than the "
        "Lightpanda/agent-browser runners' 300s default.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output dir (default: results/<suite>-mcp/<backend>/<timestamp>/)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip task ids already present in <out-dir>/predictions.jsonl",
    )
