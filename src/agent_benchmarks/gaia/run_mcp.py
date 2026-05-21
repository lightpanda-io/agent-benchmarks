"""
GAIA runner that drives a browser MCP server through `claude -p`.

Mirrors `assistantbench-mcp-run` for GAIA: hold the LLM brain (Claude)
constant; vary only the browser MCP (`--backend lightpanda`,
`--backend agent-browser`, or `--backend browser-use`). Writes the same
predictions.jsonl envelope as `gaia-run`, so `gaia-grade` reads it without
modification.

Attachment handling matches `gaia/run_ab.py`:

  - DOCX/XLSX/PPTX → extract via the Lightpanda runner's `_extract_office_text`.
  - Other readable text/code (UTF-8 decodes, ≤ MAX_INLINE_BYTES) → inline raw.
  - Binary (PDF/PNG/MP3/etc.) → skip the body, leave a marker. Tasks with
    binary attachments score 0, which matches Lightpanda's effective
    behavior today and keeps the 53-task denominator comparable.

Prerequisites:
  - HF_TOKEN (accept terms at https://huggingface.co/datasets/gaia-benchmark/GAIA)
  - `claude` CLI on PATH (or pass --claude)
  - For --backend lightpanda: a built lightpanda binary
  - For --backend agent-browser: agent-browser installed
  - For --backend browser-use: `uv sync` (pulls in browser-use) + a system
    Chrome/Chromium binary on PATH

Example:

    HF_TOKEN=... uv run gaia-mcp-run --backend lightpanda --limit 3
    HF_TOKEN=... uv run gaia-mcp-run --backend agent-browser --workers 2
    HF_TOKEN=... uv run gaia-mcp-run --backend browser-use --workers 2
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from datasets import load_dataset  # type: ignore[import-not-found]

from .._mcp import (
    add_common_mcp_args,
    allowed_tools_for,
    build_mcp_config,
    cleanup_browser_use_profiles,
    close_agent_browser_sessions,
    drain_pool,
    ensure_browser_use_prereqs,
    ensure_executable,
    make_session_pool,
    resolve_agent_browser_bin,
    resolve_claude_bin,
    resolve_lightpanda_bin,
    run_mcp_task,
)
from ..common import (
    emit_scores,
    load_completed_ids,
    resolve_out_dir,
    run_benchmark_tasks,
    write_run_manifest,
)
from .grade import grade_predictions

# Read-only reuse of the attachment-inlining logic from the agent-browser
# GAIA runner. Keeps a single source of truth for office/text/binary
# classification. We build the user message locally though — `_build_message`
# bakes in agent-browser-chat-specific suite instructions + a strict-format
# tail that conflicts with the <ANSWER> envelope this runner uses.
from .run_ab import _read_attachment_for_inline  # noqa: PLC2701

# benchmarks/src/agent_benchmarks/gaia/run_mcp.py → benchmarks/
PROJECT_ROOT = Path(__file__).resolve().parents[3]

SYSTEM_PROMPT = """\
You are a research assistant driving a headless browser via MCP tools on the GAIA QA benchmark.

The browser MCP tools are the ONLY way you can access the web. There is no WebSearch, no WebFetch, no shortcut — you must navigate real pages. Tool surfaces differ per backend; list the tools you have to discover them.

BE PERSISTENT — this is the load-bearing instruction:
- GAIA tasks expect multi-step browsing. Most need 20-50 tool calls; some need 100+. Answers from prior knowledge without browsing score 0.
- If a search returns poor results, try DIFFERENT phrasings — synonyms, narrower queries, different angles.
- If a page is unreachable, find a DIFFERENT source. Wikipedia, official sites, archived pages, news outlets.
- If extraction fails, try a different tool (markdown → tree → extract → snapshot).
- Do NOT respond "unknown" or fall back to prior knowledge until you have made at least 20 substantive tool calls AND tried at least 3 different sources/angles.
- Small-candidate questions ("A, B, or C", yes/no): always pick one — never abstain.

Strategy:
1. Plan: prefer authoritative direct sources (Wikipedia, official sites) over search-engine landing pages when you know where to go.
2. Search: use the MCP search tool when available, or open https://duckduckgo.com/?q=... when not.
3. Navigate the available browser tools. Re-inspect after page-changing actions — DOM snapshots / refs go stale.
4. Cross-check on a second source where the answer is non-obvious.

Final-answer envelope — STRICT
================================
Your entire response will be discarded except for the LAST text wrapped in `<ANSWER>...</ANSWER>` tags. Reasoning, tool-call narration, partial-credit notes — anything outside the envelope — is ignored. Only the envelope contents are graded.

GAIA grades by exact match after normalization (lowercase, strip articles/punct).

Format INSIDE the envelope:
- No preface, no explanation, no markdown, no source citations.
- Numbers: bare digits — no comma separators, no currency unless asked, no units beyond what's asked.
- Names/titles: complete and verbatim, no decoration, no surrounding punctuation.
- Lists: comma-separated on one line.

Examples (good):
  <ANSWER>45</ANSWER>
  <ANSWER>Oko, Thief of Crowns</ANSWER>
  <ANSWER>Paris, London, Tokyo</ANSWER>

Examples (bad — these score 0):
  <ANSWER>**45**</ANSWER>                              ← markdown inside
  <ANSWER>The answer is 45.</ANSWER>                   ← preface inside
  <ANSWER>$45</ANSWER>                                 ← currency
  <ANSWER>1,234</ANSWER>                               ← comma separator
"""


def _build_task_message(task: str, attachment_path: Path | None) -> tuple[str, str | None]:
    """Build the user message: task + inlined attachment (if any) + envelope reminder.

    Avoid `run_ab._build_message` because it prepends agent-browser-chat
    suite instructions and a strict-format tail that conflict with the
    <ANSWER> envelope used here.
    """
    tail = (
        "\n\nWrap your final answer in <ANSWER>...</ANSWER>. Inside the envelope: "
        "bare digits for numbers, comma-separated on one line for lists, no markdown."
    )
    if attachment_path is None:
        return f"Task: {task}{tail}", None

    body, status = _read_attachment_for_inline(attachment_path)
    name = attachment_path.name
    if body is None:
        attach_block = (
            f"\n\n[Attachment present: {name} — content not readable in text-only "
            f"mode ({status}). Answer from the question text and prior knowledge.]"
        )
    else:
        attach_block = (
            f"\n\n--- Attachment: {name} ({status}) ---\n{body}\n--- end attachment ---"
        )
    return f"Task: {task}{attach_block}{tail}", status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_mcp_args(parser)
    parser.add_argument(
        "--level",
        type=int,
        default=1,
        choices=[1, 2, 3],
        help="GAIA level (default: 1)",
    )
    parser.add_argument(
        "--skip-attachments",
        action="store_true",
        help="Skip rows with attached files entirely. Default is to include them, "
        "inlining what's readable as text — matches Lightpanda's behavior so "
        "headline numbers stay comparable.",
    )
    args = parser.parse_args(argv)

    claude_bin = resolve_claude_bin(args.claude)
    if not ensure_executable(claude_bin):
        print(f"error: claude binary not found at {claude_bin!r}", file=sys.stderr)
        return 2

    lightpanda_bin: Path | None = None
    agent_browser_bin: str | None = None
    if args.backend == "lightpanda":
        lightpanda_bin = resolve_lightpanda_bin(args.lightpanda, PROJECT_ROOT)
        if not lightpanda_bin.exists():
            print(f"error: lightpanda binary not found at {lightpanda_bin}", file=sys.stderr)
            return 2
    elif args.backend == "agent-browser":
        agent_browser_bin = resolve_agent_browser_bin(args.agent_browser)
        if not ensure_executable(agent_browser_bin):
            print(
                f"error: agent-browser binary not found at {agent_browser_bin!r}",
                file=sys.stderr,
            )
            return 2
    elif args.backend == "agent-browser-lightpanda":
        agent_browser_bin = resolve_agent_browser_bin(args.agent_browser)
        if not ensure_executable(agent_browser_bin):
            print(
                f"error: agent-browser binary not found at {agent_browser_bin!r}",
                file=sys.stderr,
            )
            return 2
        lightpanda_bin = resolve_lightpanda_bin(args.lightpanda, PROJECT_ROOT)
        if not lightpanda_bin.exists():
            print(f"error: lightpanda binary not found at {lightpanda_bin}", file=sys.stderr)
            return 2
    elif args.backend == "browser-use":
        ok, hint = ensure_browser_use_prereqs()
        if not ok:
            print(f"error: {hint}", file=sys.stderr)
            return 2

    out_dir = resolve_out_dir(args.out_dir, PROJECT_ROOT, f"gaia-mcp/{args.backend}")
    predictions_path = out_dir / "predictions.jsonl"
    write_run_manifest(
        out_dir,
        agent_provider=f"claude-mcp-{args.backend}",
        agent_model=args.model,
    )

    completed = load_completed_ids(predictions_path) if args.resume else set()
    if completed:
        print(f"Resuming — skipping {len(completed)} already-completed task(s)", file=sys.stderr)

    config = f"2023_level{args.level}"
    print(f"Loading gaia-benchmark/GAIA {config}/{args.split} from HuggingFace...", file=sys.stderr)
    if not os.environ.get("HF_TOKEN") and not os.environ.get("HUGGING_FACE_HUB_TOKEN"):
        print(
            "warning: no HF_TOKEN — GAIA is a gated dataset and load will fail.",
            file=sys.stderr,
        )
    ds = load_dataset("gaia-benchmark/GAIA", config, split=args.split)
    rows: list[dict[str, Any]] = list(ds)

    snapshot_dir: Path | None = None
    if not args.skip_attachments and any((r.get("file_name") or "").strip() for r in rows):
        from huggingface_hub import snapshot_download  # type: ignore[import-not-found]

        print("Downloading GAIA dataset snapshot for attachments...", file=sys.stderr)
        snapshot_dir = Path(snapshot_download(repo_id="gaia-benchmark/GAIA", repo_type="dataset"))

    if args.skip_attachments:
        before = len(rows)
        rows = [r for r in rows if not (r.get("file_name") or "").strip()]
        print(
            f"Skipped attachments: {before - len(rows)}/{before} rows dropped (kept {len(rows)})",
            file=sys.stderr,
        )

    if args.limit is not None:
        rows = rows[: args.limit]

    pending = [r for r in rows if r["task_id"] not in completed]

    pool = make_session_pool(args.workers, prefix=f"gaia-mcp-{args.backend}")

    def _work(row: dict[str, Any]) -> dict[str, Any]:
        attachment_path: Path | None = None
        file_name = (row.get("file_name") or "").strip()
        if file_name and snapshot_dir is not None:
            candidate = snapshot_dir / row["file_path"]
            if candidate.exists():
                attachment_path = candidate

        task_message, attach_status = _build_task_message(row["Question"], attachment_path)

        session = pool.get()
        try:
            cfg = build_mcp_config(
                args.backend,
                lightpanda_bin=lightpanda_bin,
                agent_browser_bin=agent_browser_bin,
                session=session,
            )
            pred, duration_s, timed_out, stderr_tail, rc, trace = run_mcp_task(
                claude_bin=claude_bin,
                backend=args.backend,
                mcp_config=cfg,
                allowed_tools=allowed_tools_for(args.backend),
                system_prompt=SYSTEM_PROMPT,
                task_prompt=task_message,
                model=args.model,
                timeout_s=args.timeout,
            )
        finally:
            pool.put(session)

        return {
            "id": row["task_id"],
            "task": row["Question"],
            "gold": row["Final answer"],
            "prediction": pred,
            "duration_s": round(duration_s, 2),
            "timed_out": timed_out,
            "returncode": rc,
            "level": row.get("Level"),
            "file_name": file_name or None,
            "attachment_status": attach_status,
            "backend": args.backend,
            "trace": trace,
            "stderr_tail": stderr_tail,
        }

    try:
        run_benchmark_tasks(
            pending,
            _work,
            predictions_path=predictions_path,
            workers=args.workers,
            timeout_s=args.timeout,
            provider=f"claude-mcp-{args.backend}",
            model=args.model,
            preview_fn=lambda row: row["Question"],
        )
    finally:
        sessions = drain_pool(pool)
        if args.backend in ("agent-browser", "agent-browser-lightpanda") and agent_browser_bin is not None:
            close_agent_browser_sessions(agent_browser_bin, sessions)
        elif args.backend == "browser-use":
            cleanup_browser_use_profiles(sessions)

    print("\nGrading...", file=sys.stderr)
    emit_scores(grade_predictions(predictions_path), out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
