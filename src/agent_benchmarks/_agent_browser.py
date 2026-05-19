"""Subprocess helper for driving Vercel agent-browser's `chat` mode as a
benchmark backend.

Mirrors the contract of `common.run_lightpanda_task` so a suite's runner can
plug either backend behind the same predictions.jsonl envelope. The Lightpanda
runner and `common.py` are intentionally untouched — this module is additive.

`agent-browser chat <msg>` is a single-shot LLM-driven loop over agent-browser's
CDP tools. Unlike Lightpanda's agent, it has no `--system-prompt` flag: the
chat command ships its own internal system prompt for tool use, so suite-level
instructions (output formatting, retrieval strategy) have to be folded into
the user message. The internal turn cap is 5 minutes — pass a subprocess
`timeout_s` >= 300 only knowing that agent-browser will still bail at 300.

Direct-Gemini mode
------------------
agent-browser's chat command speaks the OpenAI Chat Completions wire format to
whatever URL is in $AI_GATEWAY_URL (default: Vercel AI Gateway). Google ships
an OpenAI-compatible endpoint at
`https://generativelanguage.googleapis.com/v1beta/openai/`, so setting
`GEMINI_DIRECT=1` + `GOOGLE_API_KEY=...` rewrites the subprocess env to point
at Google directly — no Vercel billing, no extra hop, no patch to chat.rs.
This is best-effort: Google's compat layer has had rough edges on tool-call
schema strictness in the past, so smoke-test before a full run.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

STDERR_TAIL_BYTES = 8 * 1024


def resolve_agent_browser_binary(arg: str | None) -> str:
    """Resolve which agent-browser binary to invoke.

    Explicit --agent-browser wins, then $AGENT_BROWSER_BIN, then PATH lookup.
    Returns the resolved string (never raises); call ensure_binary() to verify
    it actually exists before launching subprocesses."""
    if arg:
        return arg
    env = os.environ.get("AGENT_BROWSER_BIN")
    if env:
        return env
    return "agent-browser"


def ensure_binary(binary: str) -> bool:
    """True iff `binary` resolves to an executable on disk or PATH."""
    if "/" in binary or "\\" in binary:
        return Path(binary).is_file() and os.access(binary, os.X_OK)
    return shutil.which(binary) is not None


def print_agent_browser_missing(binary: str) -> None:
    print(f"error: agent-browser binary not found at {binary!r}", file=sys.stderr)
    print(
        "hint: install with `npm install -g agent-browser && agent-browser install`,\n"
        "      or set $AGENT_BROWSER_BIN to a checkout's release binary.",
        file=sys.stderr,
    )


def make_session_pool(workers: int, prefix: str) -> queue.Queue[str]:
    """One stable session name per worker, reused across tasks so we pay
    Chrome startup once per worker rather than once per task. Workers borrow
    via .get() and return via .put() in a try/finally."""
    q: queue.Queue[str] = queue.Queue()
    for i in range(max(1, workers)):
        q.put(f"{prefix}-w{i}")
    return q


def close_sessions(binary: str, sessions: list[str]) -> None:
    """Best-effort `agent-browser --session <name> close` for each session
    drained from a pool. Errors are swallowed — the daemon will idle out
    anyway, and we don't want to mask a real benchmark failure here."""
    for name in sessions:
        with contextlib.suppress(subprocess.SubprocessError, OSError):
            subprocess.run(
                [binary, "--session", name, "close"],
                capture_output=True,
                timeout=10,
                check=False,
            )


def drain_pool(pool: queue.Queue[str]) -> list[str]:
    drained: list[str] = []
    while True:
        try:
            drained.append(pool.get_nowait())
        except queue.Empty:
            return drained


GEMINI_OPENAI_COMPAT_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


def gemini_direct_enabled() -> bool:
    return os.environ.get("GEMINI_DIRECT", "").strip() not in ("", "0", "false", "False")


def _strip_provider_prefix(model: str | None) -> str | None:
    """In direct-Gemini mode, agent-browser sends the model name unchanged to
    Google's OpenAI-compat endpoint, which does NOT understand the Vercel-style
    `google/` prefix. Strip it so `google/gemini-3-flash` and `gemini-3-flash`
    both work."""
    if model and model.startswith("google/"):
        return model[len("google/") :]
    return model


def run_agent_browser_task(
    *,
    binary: str,
    session: str,
    model: str | None,
    message: str,
    timeout_s: float,
) -> tuple[str, float, bool, str, int | None]:
    """Run a single task through `agent-browser chat -q`.

    Returns (prediction, duration_s, timed_out, stderr_tail, returncode).
    No tool-trace field — agent-browser's chat output format isn't the same
    `[tool: ...] / [result: ...]` shape Lightpanda emits, and graders never
    look at traces anyway.
    """
    effective_model = _strip_provider_prefix(model) if gemini_direct_enabled() else model

    # Use --json instead of -q. agent-browser's -q (Quiet) suppresses BOTH
    # tool-call output and the AI's final text (chat.rs:449 gates printing on
    # verbosity != Quiet), so -q leaves stdout empty. --json on the other hand
    # emits exactly one JSON object on stdout at end-of-run:
    #   {"success": true, "text": "<all_text>", "tool_calls": [...]}
    # or {"success": false, "error": "..."} on failure. Tool calls go to
    # stderr in non-quiet, so --json gives us a clean stdout to parse.
    cmd: list[str] = [binary, "--session", session, "--json"]
    if effective_model:
        cmd += ["--model", effective_model]
    cmd += ["chat", message]

    env = dict(os.environ)

    if gemini_direct_enabled():
        google_key = os.environ.get("GOOGLE_API_KEY", "").strip()
        # Rewrite the subprocess env only — never touch the parent process's
        # env so the caller's outer state (e.g. interactive shells, other
        # suites) stays clean.
        env["AI_GATEWAY_URL"] = GEMINI_OPENAI_COMPAT_URL
        env["AI_GATEWAY_API_KEY"] = google_key
        # AI_GATEWAY_MODEL is read as a default model name when --model isn't
        # passed (flags.rs:503). Strip the prefix there too so a user who
        # exported `AI_GATEWAY_MODEL=google/gemini-3-flash` for the gateway
        # path doesn't break direct-Gemini.
        if "AI_GATEWAY_MODEL" in env:
            stripped = _strip_provider_prefix(env["AI_GATEWAY_MODEL"])
            if stripped is not None:
                env["AI_GATEWAY_MODEL"] = stripped

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

    prediction, error_from_json = _parse_chat_json_stdout(stdout)

    # In --json mode, agent-browser routes ALL errors to stdout as the JSON
    # error envelope and prints nothing to stderr — so a failing run leaves
    # stderr empty and the prediction silently blank. Splice the error
    # message into the stderr_tail field so it lands in predictions.jsonl
    # next to the empty prediction.
    if error_from_json:
        stderr = (stderr or "") + f"\n[chat --json error]\n{error_from_json}\n"

    if len(stderr) > STDERR_TAIL_BYTES:
        stderr_tail = "...[truncated]...\n" + stderr[-STDERR_TAIL_BYTES:]
    else:
        stderr_tail = stderr

    return prediction, duration_s, timed_out, stderr_tail, returncode


def _parse_chat_json_stdout(stdout: str) -> tuple[str, str | None]:
    """Extract the AI's text response from agent-browser's `chat --json` stdout.

    Returns (prediction, error_message). `error_message` is non-None when
    chat reported `success: false`, so callers can surface the message
    instead of swallowing it (chat sends NOTHING to stderr in --json mode,
    so without this the failure is invisible in predictions.jsonl).

    Expected shape on success:
        {"success": true, "text": "...", "tool_calls": [...]}
    On failure (auth, gateway error, internal timeout):
        {"success": false, "error": "..."}

    `text` is the concatenation of text deltas from EVERY turn in the chat
    loop, not just the final assistant message. For benchmarks where the
    system prompt tells the model to emit only the final answer, this is
    fine — well-behaved models tool-call silently and only speak on the
    final turn. If a model emits chatter on intermediate turns, those chunks
    will land in the prediction and likely tank the strict-match score.
    """
    stripped = stdout.strip()
    if not stripped:
        return "", None

    # agent-browser emits exactly one JSON object on stdout per --json run
    # (chat.rs:379, single trailing println!). Decode the whole thing — do NOT
    # split on newlines, because the model's text response often contains
    # embedded \n characters (which JSON encodes literally inside the string),
    # and `splitlines()` would shred the object mid-string-value.
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        # Not valid JSON — fall back to the raw stdout. Better than dropping
        # output silently if the contract drifts.
        return stripped, None

    if not isinstance(obj, dict):
        return stripped, None
    if obj.get("success") is False:
        err = obj.get("error", "")
        return "", str(err) if err else "chat --json reported success=false with no message"
    text = obj.get("text", "")
    if not isinstance(text, str):
        return "", None
    return text.strip(), None


def add_common_agent_browser_args(parser: Any) -> None:
    """Flags every agent-browser-backed runner accepts. Suite-specific flags
    are still added separately by the caller (mirrors add_common_runner_args)."""
    parser.add_argument(
        "--agent-browser",
        default=None,
        help="Path to agent-browser binary (default: $AGENT_BROWSER_BIN or PATH lookup)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="AI Gateway model id (e.g. anthropic/claude-sonnet-4.6). "
        "Defaults to whatever agent-browser picks (currently claude-sonnet-4.6).",
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
        help="Parallel agent-browser subprocesses (each gets its own --session + Chrome)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Per-task subprocess timeout in seconds. agent-browser caps each "
        "chat turn at 300s internally, so values above 300 don't extend a "
        "stuck turn — they just give the daemon more time to wind down.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output dir (default: results/<suite>-ab/<timestamp>/)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip task ids already present in <out-dir>/predictions.jsonl",
    )
