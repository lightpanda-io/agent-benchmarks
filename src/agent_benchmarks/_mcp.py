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
import tempfile
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

# browser-use's MCP server tools. Three tools are deliberately omitted from
# the allow-list to keep the comparison clean:
#
#   - retry_with_browser_use_agent: delegates the task to browser-use's OWN
#     LLM loop using the agent's task prompt. If allowed, Claude can call it
#     once and have browser-use's internal Gemini/Claude solve the problem,
#     making "Claude+MCP" effectively "browser-use's agent" and contaminating
#     the comparison.
#   - browser_screenshot: returns an image. Multimodal input would give
#     browser-use an unfair signal channel that Lightpanda (text-only, no
#     rendering) and agent-browser (text accessibility tree) can't match.
#     We pair this with `browser_get_state(include_screenshot=false)` (the
#     default) so the surface stays text-only.
#   - browser_list_sessions / browser_close_session / browser_close_all:
#     session-management bookkeeping the agent doesn't need — the stdio
#     subprocess teardown handles cleanup when claude exits.
BROWSER_USE_TOOLS: tuple[str, ...] = (
    "browser_navigate",
    "browser_click",
    "browser_type",
    "browser_get_state",
    "browser_extract_content",
    "browser_get_html",
    "browser_scroll",
    "browser_go_back",
    "browser_list_tabs",
    "browser_switch_tab",
    "browser_close_tab",
)

# MCP server-name keys. These show up in `mcp__<key>__<tool>` allow-list
# entries, so keep them stable.
SERVER_NAME = {
    "lightpanda": "lightpanda",
    "agent-browser": "agent-browser",
    "browser-use": "browser-use",
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


def ensure_browser_use_prereqs(python_bin: str | None = None) -> tuple[bool, str]:
    """Check the browser-use MCP backend can launch.

    Two things must be in place:
      1. The `browser_use` package importable in `python_bin` (defaults to
         this interpreter, which is the venv when run via `uv run`).
      2. A system Chrome / Chromium binary on PATH — browser-use uses cdp-use
         (not Playwright) and probes `which google-chrome / chromium`.

    Returns (ok, hint_message). Hint is empty on success; on failure it's a
    user-facing one-or-two-line message safe to print directly.
    """
    python = python_bin or sys.executable
    try:
        proc = subprocess.run(
            [python, "-c", "import browser_use"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"failed to spawn {python!r} to verify browser-use: {e!r}"
    if proc.returncode != 0:
        return False, (
            f"{python} cannot import `browser_use` "
            f"(stderr: {proc.stderr.strip()[:200]}).\n"
            "  Did you `uv sync`? The benchmarks pyproject pulls browser-use in."
        )

    for cmd in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if shutil.which(cmd):
            return True, ""
    return False, (
        "no Chrome/Chromium binary found on PATH. browser-use needs a system "
        "Chrome install — try `sudo apt install chromium` (Debian/Ubuntu) or "
        "install Google Chrome from https://www.google.com/chrome/."
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
    Lightpanda state. For browser-use the per-worker isolation comes from
    spawning a fresh `python -m browser_use.mcp.server` per claude call
    (each subprocess gets its own Chromium); the session string is unused
    there but kept for signature symmetry.
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
    if backend == "browser-use":
        python = python_bin or sys.executable
        per_worker_config_dir = _prepare_browser_use_worker_dir(session)
        return {
            "mcpServers": {
                SERVER_NAME["browser-use"]: {
                    "type": "stdio",
                    "command": python,
                    "args": ["-m", "browser_use.mcp.server"],
                    "env": {
                        "BROWSER_USE_LOGGING_LEVEL": "critical",
                        "BROWSER_USE_SETUP_LOGGING": "false",
                        "ANONYMIZED_TELEMETRY": "false",
                        "BROWSER_USE_TELEMETRY_ENABLED": "false",
                        "BROWSER_USE_CONFIG_DIR": str(per_worker_config_dir),
                        # LOAD-BEARING: the MCP server's profile_data dict
                        # has `headless: False` hardcoded (mcp/server.py:592).
                        # `_load_config()` reads BROWSER_USE_HEADLESS and the
                        # per-worker config.json we wrote above splats it
                        # into the BrowserProfile via `**profile_config`.
                        "BROWSER_USE_HEADLESS": "true",
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
    elif backend == "browser-use":
        server = SERVER_NAME["browser-use"]
        tools = BROWSER_USE_TOOLS
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


# MCP-tool deny-list per backend. `--allowed-tools` is only a permission-prompt
# bypass list in `claude -p` mode (NOT a hard restriction) — for built-in tools
# we deny via DISALLOWED_BUILTIN_TOOLS, and we need the same hard deny for MCP
# tools the comparison must exclude. Without these entries, Claude happily
# called `mcp__browser-use__browser_screenshot` 47× and
# `mcp__browser-use__retry_with_browser_use_agent` 31× in the first browser-use
# run even though they were absent from --allowed-tools.
#
# Lightpanda's surface has no equivalents to exclude — `extract` etc. are
# purely structural and intended to be available. agent-browser likewise has
# no LLM-backed or multimodal tool to hide. Only browser-use's MCP server
# ships tools that would contaminate the text-only-parity comparison.
DISALLOWED_MCP_TOOLS_BY_BACKEND: dict[str, tuple[str, ...]] = {
    "lightpanda": (),
    "agent-browser": (),
    "browser-use": (
        # Multimodal — text-only-parity violation.
        "mcp__browser-use__browser_screenshot",
        # Delegates the entire task to browser-use's internal LLM loop —
        # would short-circuit the Sonnet brain we're comparing.
        "mcp__browser-use__retry_with_browser_use_agent",
        # Session-management housekeeping the agent doesn't need; not
        # contamination per se, just noise.
        "mcp__browser-use__browser_list_sessions",
        "mcp__browser-use__browser_close_session",
        "mcp__browser-use__browser_close_all",
    ),
}


def disallowed_mcp_tools(backend: str) -> str:
    return ",".join(DISALLOWED_MCP_TOOLS_BY_BACKEND.get(backend, ()))


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


def _prepare_browser_use_worker_dir(session: str) -> Path:
    """Create per-worker config + profile dirs for browser-use's MCP server.

    Without this, parallel workers ALL try to read+lock the same source
    user-data-dir at ~/.config/browseruse/profiles/default. Only the first
    worker's Chrome wins the SingletonLock; the others hang indefinitely
    in `_copy_profile()` waiting for the lock. Observed in the wild:
    workers=4 → only 1 Chrome ever spawns, the other 3 MCP servers sit at
    ~1% CPU forever.

    The MCP server hardcodes `user_data_dir='~/.config/browseruse/profiles/default'`
    in its profile_data dict (mcp/server.py:589) and there's no
    BROWSER_USE_USER_DATA_DIR env override. The path it DOES read is the
    config.json (loaded via BROWSER_USE_CONFIG_DIR → load_config()), and
    `**profile_config` from that config splats over the hardcoded default.
    So we write a per-worker config.json with a per-worker user_data_dir.
    Each worker then drives a distinct Chrome profile, no lock contention.

    Returns the per-worker config dir to use as BROWSER_USE_CONFIG_DIR.
    Idempotent — repeated calls within the same worker session reuse the
    same dir. The cleanup_browser_use_profiles() helper rm-rfs these at
    the end of a run.
    """
    worker_root = Path(tempfile.gettempdir()) / f"browser-use-mcp-{session}"
    profile_dir = worker_root / "profiles" / "default"
    profile_dir.mkdir(parents=True, exist_ok=True)
    # config.json schema: see browser_use.config.DBStyleConfigJSON.
    # The MCP server's _load_config() flattens the default browser_profile
    # entry into a dict of field values. user_data_dir here overrides the
    # hardcoded MCP default; headless is also set defensively (the
    # BROWSER_USE_HEADLESS env var sets it too — belt-and-suspenders so a
    # future browser-use bump that drops the env hook still stays headless).
    profile_id = f"bench-{session}"
    config_doc = {
        "browser_profile": {
            profile_id: {
                "id": profile_id,
                "default": True,
                "user_data_dir": str(profile_dir),
                "headless": True,
            }
        },
        "llm": {},
        "agent": {},
    }
    (worker_root / "config.json").write_text(json.dumps(config_doc, indent=2))
    return worker_root


def cleanup_browser_use_profiles(sessions: list[str]) -> None:
    """Remove the per-worker browser-use profile dirs created by
    build_mcp_config(backend="browser-use"). Each one is ~100 MB of Chrome
    profile data — a 4-worker, 53-task run accumulates ~400 MB in /tmp
    that would otherwise stick around until reboot. Best-effort, errors
    swallowed (the OS cleans /tmp eventually anyway)."""
    for name in sessions:
        path = Path(tempfile.gettempdir()) / f"browser-use-mcp-{name}"
        with contextlib.suppress(OSError):
            shutil.rmtree(path, ignore_errors=True)


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
        # ONLY way Claude can do work, AND any MCP tools the comparison must
        # exclude (e.g. browser-use's screenshot and retry_with_agent). See
        # DISALLOWED_BUILTIN_TOOLS / DISALLOWED_MCP_TOOLS_BY_BACKEND — both
        # are load-bearing (--allowed-tools is permission-prompt bypass only,
        # not a hard restriction, in `claude -p` mode).
        "--disallowed-tools",
        ",".join(
            t for t in (disallowed_builtin_tools(), disallowed_mcp_tools(backend)) if t
        ),
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
        choices=["lightpanda", "agent-browser", "browser-use"],
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
