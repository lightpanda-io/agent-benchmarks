"""
GAIA runner backed by Vercel agent-browser instead of Lightpanda.

Loads GAIA from HuggingFace (gated; needs `HF_TOKEN`), invokes
`agent-browser chat -q -- <message>` one-shot per row through a per-worker
session pool, and writes the same predictions.jsonl envelope as `gaia-run`
so the existing grader (`gaia-grade`) scores it without modification.

Attachment handling
-------------------
agent-browser has no `--attach` equivalent. The chat command is a
single text turn, so attachments have to be inlined into the user message.
We match Lightpanda's "text-only effective behavior" by:

  - DOCX/XLSX/PPTX → reuse `_extract_office_text` from the Lightpanda runner.
  - Other readable text/code (UTF-8 decodes, ≤ MAX_INLINE_BYTES) → inline raw.
  - Binary (PDF/PNG/MP3/etc.) → skip the body, leave a marker. Those tasks
    score 0, which matches what Lightpanda does on the same 11 attachment
    tasks today and keeps the 53-task denominator comparable.

Direct-Gemini mode (no Vercel billing) — see `_agent_browser.py` docstring.

Prerequisites:
  - HF_TOKEN (accept terms at https://huggingface.co/datasets/gaia-benchmark/GAIA)
  - `agent-browser` on PATH (or pass --agent-browser, or set $AGENT_BROWSER_BIN)
  - One of:
      AI_GATEWAY_API_KEY=gw_...                          (Vercel gateway)
      GEMINI_DIRECT=1  +  GOOGLE_API_KEY=...             (direct to Google)

Example:

    HF_TOKEN=... AI_GATEWAY_API_KEY=gw_... uv run gaia-ab-run --limit 3
    HF_TOKEN=... GEMINI_DIRECT=1 GOOGLE_API_KEY=... \\
      uv run gaia-ab-run --workers 2 --model gemini-3.5-flash
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from datasets import load_dataset  # type: ignore[import-not-found]

from .._agent_browser import (
    add_common_agent_browser_args,
    close_sessions,
    drain_pool,
    ensure_binary,
    gemini_direct_enabled,
    make_session_pool,
    print_agent_browser_missing,
    resolve_agent_browser_binary,
    run_agent_browser_task,
)
from ..common import (
    emit_scores,
    load_completed_ids,
    resolve_out_dir,
    run_benchmark_tasks,
    write_run_manifest,
)
from .grade import grade_predictions

# Read-only reuse of office-doc extraction from the Lightpanda runner. We
# don't modify .run; we just borrow the helper so we don't reimplement the
# same docx/xlsx/pptx → text logic.
from .run import OFFICE_EXTS, _extract_office_text  # noqa: PLC2701

# benchmarks/src/agent_benchmarks/gaia/run_ab.py → benchmarks/
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Cap inlined attachment bodies. GAIA's Level-1 text/code attachments are
# small (a few KB), but raw HTML or generated CSV can blow past that. Going
# over the cap is a quality bug, not a correctness bug — the model still gets
# the question, just without the full attachment context.
MAX_INLINE_BYTES = 64 * 1024

# Extensions to attempt as text. Anything not in this set AND not in
# OFFICE_EXTS is treated as binary (skipped with a marker). The "try UTF-8
# decode" path below also catches text files with unusual extensions.
TEXT_EXTS = {
    ".txt",
    ".py",
    ".json",
    ".jsonl",
    ".csv",
    ".tsv",
    ".md",
    ".xml",
    ".yaml",
    ".yml",
    ".html",
    ".htm",
    ".log",
    ".ini",
    ".cfg",
    ".sh",
    ".js",
    ".ts",
    ".rs",
    ".go",
    ".c",
    ".cpp",
    ".h",
    ".java",
}

SUITE_INSTRUCTIONS = """\
You are a research assistant answering a GAIA-style web QA question with the agent-browser tools.

Rules for the final answer (the answer is graded by exact match after normalization):
- Output ONLY the exact value requested. No preface ("Based on...", "The answer is..."), no explanation, no caveats, no source citations.
- No markdown: no **bold**, no *italics*, no `code`, no bullets, no headings, no asterisks anywhere in the final reply.
- Numbers: bare digits — no comma separators, no currency unless asked, no units beyond what's asked.
- Names/titles: complete and verbatim, no decoration, no surrounding punctuation.
- Lists: comma-separated on one line.
- DO: `45` / `Oko, Thief of Crowns` / `Paris, London, Tokyo`
- DON'T: `**45**` / `The answer is 45.` / `* Paris\\n* London\\n* Tokyo`

Strategy:
- Prefer authoritative direct sources (Wikipedia, official sites) when you know where to go.
- Small-candidate questions ("A, B, or C", yes/no): always pick one — never abstain.
- Be decisive. If a site is unreachable or a tool repeatedly fails, commit to your best-effort answer from prior knowledge rather than continuing to retry.
- Only respond "unknown" if you have exhausted browsing AND prior knowledge gives no lead.
"""

TASK_PROMPT_HEAD = "{instructions}\n\n---\n\nTask: {task}"

TASK_PROMPT_TAIL = (
    "\n\n"
    "Output ONLY the exact answer — no preface, no explanation, no markdown, "
    "no units beyond what's asked. Bare digits for numbers."
)


def _read_attachment_for_inline(path: Path) -> tuple[str | None, str]:
    """Return (inlined_body, status). status is one of:
    - "office:<ext>"     — extracted text
    - "text:<ext>"       — raw decoded text
    - "skipped:<reason>" — binary or unreadable; inlined_body is None
    """
    ext = path.suffix.lower()
    if ext in OFFICE_EXTS:
        try:
            text = _extract_office_text(path)
            if len(text.encode("utf-8", errors="replace")) > MAX_INLINE_BYTES:
                text = text[: MAX_INLINE_BYTES // 4] + "\n...[truncated]..."
            return text, f"office:{ext}"
        except Exception as e:
            return None, f"skipped:office-extract-failed:{e!r}"

    # Try a UTF-8 read for anything that looks textual by extension OR is
    # small enough to be worth probing.
    try:
        size = path.stat().st_size
    except OSError as e:
        return None, f"skipped:stat-failed:{e!r}"

    if ext not in TEXT_EXTS and size > MAX_INLINE_BYTES:
        return None, f"skipped:unknown-ext:{ext}"

    try:
        body = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None, f"skipped:binary:{ext or 'no-ext'}"

    if len(body.encode("utf-8", errors="replace")) > MAX_INLINE_BYTES:
        body = body[:MAX_INLINE_BYTES] + "\n...[truncated]..."
    return body, f"text:{ext or 'no-ext'}"


def _build_message(task: str, attachment_path: Path | None) -> tuple[str, str | None]:
    """Build the user-message body. Returns (message, attachment_status)."""
    head = TASK_PROMPT_HEAD.format(instructions=SUITE_INSTRUCTIONS, task=task)
    if attachment_path is None:
        return head + TASK_PROMPT_TAIL, None

    body, status = _read_attachment_for_inline(attachment_path)
    name = attachment_path.name
    if body is None:
        attach_block = (
            f"\n\n[Attachment present: {name} — content not readable in text-only "
            f"mode ({status}). Answer from the question text and prior knowledge.]"
        )
    else:
        attach_block = f"\n\n--- Attachment: {name} ({status}) ---\n{body}\n--- end attachment ---"
    return head + attach_block + TASK_PROMPT_TAIL, status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_agent_browser_args(parser)
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
        "inline what's readable as text, and let binary attachments score 0 — "
        "matches Lightpanda's behavior so the headline numbers stay comparable.",
    )
    args = parser.parse_args(argv)

    binary = resolve_agent_browser_binary(args.agent_browser)
    if not ensure_binary(binary):
        print_agent_browser_missing(binary)
        return 2

    if gemini_direct_enabled():
        if not os.environ.get("GOOGLE_API_KEY"):
            print(
                "error: GEMINI_DIRECT=1 is set but GOOGLE_API_KEY is empty.\n"
                "  export GOOGLE_API_KEY=...",
                file=sys.stderr,
            )
            return 2
        print(
            "GEMINI_DIRECT=1 — routing agent-browser chat through Google's "
            "OpenAI-compat endpoint (no Vercel AI Gateway).",
            file=sys.stderr,
        )
    elif not os.environ.get("AI_GATEWAY_API_KEY"):
        print(
            "error: AI_GATEWAY_API_KEY is not set — agent-browser chat will refuse to run.\n"
            "  export AI_GATEWAY_API_KEY=gw_...\n"
            "  (or set GEMINI_DIRECT=1 + GOOGLE_API_KEY=... to bypass the gateway)",
            file=sys.stderr,
        )
        return 2

    out_dir = resolve_out_dir(args.out_dir, PROJECT_ROOT, "gaia-ab")
    predictions_path = out_dir / "predictions.jsonl"
    write_run_manifest(out_dir, agent_provider="agent-browser", agent_model=args.model)

    completed = load_completed_ids(predictions_path) if args.resume else set()
    if completed:
        print(f"Resuming — skipping {len(completed)} already-completed task(s)", file=sys.stderr)

    config = f"2023_level{args.level}"
    print(f"Loading gaia-benchmark/GAIA {config}/{args.split} from HuggingFace...", file=sys.stderr)
    if not os.environ.get("HF_TOKEN") and not os.environ.get("HUGGING_FACE_HUB_TOKEN"):
        print(
            "warning: no HF_TOKEN in env — GAIA is a gated dataset and this load will fail.\n"
            "  1. Accept terms at https://huggingface.co/datasets/gaia-benchmark/GAIA\n"
            "  2. export HF_TOKEN=hf_...",
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

    pool = make_session_pool(args.workers, prefix="ab-bench-gaia")

    def _work(row: dict[str, Any]) -> dict[str, Any]:
        attachment_path: Path | None = None
        file_name = (row.get("file_name") or "").strip()
        if file_name and snapshot_dir is not None:
            candidate = snapshot_dir / row["file_path"]
            if candidate.exists():
                attachment_path = candidate

        message, attach_status = _build_message(row["Question"], attachment_path)

        session = pool.get()
        try:
            pred, duration_s, timed_out, stderr_tail, rc = run_agent_browser_task(
                binary=binary,
                session=session,
                model=args.model,
                message=message,
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
            "trace": [],
            "stderr_tail": stderr_tail,
        }

    try:
        run_benchmark_tasks(
            pending,
            _work,
            predictions_path=predictions_path,
            workers=args.workers,
            timeout_s=args.timeout,
            provider="agent-browser",
            model=args.model,
            preview_fn=lambda row: row["Question"],
        )
    finally:
        close_sessions(binary, drain_pool(pool))

    print("\nGrading...", file=sys.stderr)
    emit_scores(grade_predictions(predictions_path), out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
