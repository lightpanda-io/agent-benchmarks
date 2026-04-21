"""
GAIA runner for the Lightpanda agent (web-browsing subset).

Loads GAIA from HuggingFace (config `2023_level1`, split `validation`),
keeps only tasks with no attached file (the pure web-browsing subset),
invokes `lightpanda agent --task` one-shot per row, and grades answers
with the normalized exact-match rubric from the GAIA paper.

The GAIA dataset is gated on HuggingFace — accept the terms at
https://huggingface.co/datasets/gaia-benchmark/GAIA and set HF_TOKEN.

Per GAIA's license, predictions.jsonl contains the question and gold
answer verbatim, so results/ MUST remain gitignored (it is, at the
benchmarks/ root .gitignore).

Example:

    HF_TOKEN=... uv run gaia-run --limit 3
    HF_TOKEN=... uv run gaia-run --workers 8
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from datasets import load_dataset  # type: ignore[import-not-found]

from ..common import (
    load_completed_ids,
    print_lightpanda_missing,
    resolve_lightpanda_binary,
    run_lightpanda_task,
    status_label,
)
from .grade import grade_predictions

OFFICE_EXTS = {".docx", ".xlsx", ".pptx"}


def _extract_office_text(path: Path) -> str:
    """Extract a plain-text representation of an office document. Returns a
    format that preserves as much structure as the model can use: tables
    emit one row per line with tab-separated cells; pptx emits per-slide
    headings. Visual-only information (cell colors, shapes) is lost —
    tasks depending on it will still score 0."""
    ext = path.suffix.lower()
    if ext == ".docx":
        from docx import Document  # type: ignore[import-not-found]

        doc = Document(str(path))
        parts: list[str] = [p.text for p in doc.paragraphs if p.text]
        for table in doc.tables:
            for row in table.rows:
                parts.append("\t".join(cell.text for cell in row.cells))
        return "\n".join(parts)
    if ext == ".xlsx":
        from openpyxl import load_workbook  # type: ignore[import-not-found]

        wb = load_workbook(str(path), data_only=True)
        out: list[str] = []
        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            out.append(f"=== Sheet: {sheet_name} ===")
            for row in sheet.iter_rows(values_only=True):
                out.append("\t".join("" if c is None else str(c) for c in row))
        return "\n".join(out)
    if ext == ".pptx":
        from pptx import Presentation  # type: ignore[import-not-found]

        prs = Presentation(str(path))
        out: list[str] = []
        for i, slide in enumerate(prs.slides, 1):
            out.append(f"=== Slide {i} ===")
            for shape in slide.shapes:
                text = getattr(shape, "text", None)
                if text:
                    out.append(text)
        return "\n".join(out)
    raise ValueError(f"unknown office extension: {ext}")


def _preprocess_attachment(path: Path) -> Path:
    """Normalize an attachment for Lightpanda. Office docs are extracted to
    plain text written to a sibling .txt tempfile so Lightpanda's text-file
    handler picks them up. Other formats pass through untouched."""
    if path.suffix.lower() not in OFFICE_EXTS:
        return path
    text = _extract_office_text(path)
    fd, tmp = tempfile.mkstemp(suffix=f"__{path.name}.txt", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        os.unlink(tmp)
        raise
    return Path(tmp)


# benchmarks/src/agent_benchmarks/gaia/run.py → benchmarks/
PROJECT_ROOT = Path(__file__).resolve().parents[3]

SYSTEM_PROMPT = """\
You are a research assistant driving the Lightpanda browser on an open-web QA benchmark.

For each task:
1. Plan: identify the most authoritative source (official website, known database, or a search engine). Prefer direct sites (Wikipedia, shipping carriers, official brand pages) over search engines when you know the source.
2. Navigate: use goto, tree, markdown, extract, findElement to inspect pages.
3. Answer with the EXACT value requested — no sentences, no explanation, no units beyond what the question asks for, no surrounding punctuation. For numbers, output the bare number (no comma separators, no currency symbol unless asked). For lists, comma-separated on one line.
4. Small-candidate questions ("A, B, or C", yes/no): always pick one — never abstain.
5. If a site returns errors, a tool call repeatedly fails, or you cannot extract the needed info, fall back to your best-effort answer from prior knowledge rather than staying empty. Model knowledge is a valid last resort — preferable to no answer.
6. Only respond "unknown" if you have exhausted navigation AND prior knowledge gives no lead.

Search-engine use:
- When using Google, always include &hl=en&gl=us in the URL (e.g. https://www.google.com/search?q=...&hl=en&gl=us) to bypass localized consent pages.

Tool-use rules:
- Never use backendNodeId with click, fill, hover, selectOption, or setChecked. Always use a CSS selector.
- Use findElement to resolve a description into a selector when needed.
- Use distinguishing attributes (value, name, position) so selectors are unique.
- For credentials, pass $LP_USERNAME / $LP_PASSWORD directly as values.
"""

TASK_PROMPT_TEMPLATE = (
    "{task}\n\n"
    "Output only the exact answer (terse). No explanation, no units beyond what's asked, no markdown."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="validation", choices=["validation", "test"])
    parser.add_argument(
        "--level",
        type=int,
        default=1,
        choices=[1, 2, 3],
        help="GAIA level (default: 1 — shortest tool-use chains, web-browsing focused)",
    )
    parser.add_argument(
        "--skip-attachments",
        action="store_true",
        help="Skip rows with attached files. Default is to include them, even though "
        "Lightpanda can't read PDFs/audio/images — skipped tasks score 0 and the full "
        "Level-N score is what the GAIA paper reports. Use this flag only when you "
        "want to isolate agent performance on text-only tasks.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Run at most N tasks")
    parser.add_argument("--workers", type=int, default=1, help="Parallel lightpanda subprocesses")
    parser.add_argument("--timeout", type=float, default=300.0, help="Per-task timeout in seconds")
    parser.add_argument(
        "--lightpanda",
        type=Path,
        default=Path("zig-out/bin/lightpanda"),
        help="Path to the lightpanda binary. If relative, tries CWD then <pyproject>/../ (default: zig-out/bin/lightpanda)",
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
        help="Output dir (default: results/gaia/<timestamp>/)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip task ids already present in <out-dir>/predictions.jsonl",
    )
    args = parser.parse_args(argv)

    lightpanda = resolve_lightpanda_binary(args.lightpanda, PROJECT_ROOT)
    if not lightpanda.exists():
        print_lightpanda_missing(lightpanda)
        return 2

    if args.out_dir is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = PROJECT_ROOT / "results" / "gaia" / ts
    else:
        out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = out_dir / "predictions.jsonl"

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

    # Materialize the dataset snapshot locally so we can point `--task-attachment`
    # at real file paths. Only needed when attachments aren't skipped.
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
    print(
        f"Running {len(pending)} task(s) with {args.workers} worker(s), "
        f"timeout {args.timeout:.0f}s, provider={args.provider}"
        + (f", model={args.model}" if args.model else ""),
        file=sys.stderr,
    )
    print(f"Output dir: {out_dir}", file=sys.stderr)

    results_lock_file = predictions_path.open("a")

    def _work(row: dict[str, Any]) -> dict[str, Any]:
        attachment: Path | None = None
        tmp_to_delete: Path | None = None
        file_name = (row.get("file_name") or "").strip()
        if file_name and snapshot_dir is not None:
            raw = snapshot_dir / row["file_path"]
            if raw.exists():
                try:
                    attachment = _preprocess_attachment(raw)
                    if attachment != raw:
                        tmp_to_delete = attachment
                except Exception as e:
                    print(
                        f"warn: preprocess failed for {raw}: {e}; passing raw path",
                        file=sys.stderr,
                    )
                    attachment = raw
        try:
            pred, duration_s, timed_out, stderr_tail, rc = run_lightpanda_task(
                lightpanda=lightpanda,
                provider=args.provider,
                model=args.model,
                user_agent=args.user_agent,
                system_prompt=SYSTEM_PROMPT,
                task_prompt=TASK_PROMPT_TEMPLATE.format(task=row["Question"]),
                attachment=attachment,
                timeout_s=args.timeout,
            )
        finally:
            if tmp_to_delete is not None:
                try:
                    tmp_to_delete.unlink()
                except OSError:
                    pass
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
            "stderr_tail": stderr_tail,
        }

    try:
        if args.workers <= 1:
            for idx, row in enumerate(pending, 1):
                result = _work(row)
                results_lock_file.write(json.dumps(result) + "\n")
                results_lock_file.flush()
                print(
                    f"[{idx}/{len(pending)}] {status_label(result)} {result['duration_s']:.1f}s {result['id'][:12]} — {row['Question'][:80]}",
                    file=sys.stderr,
                )
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
                futures = {ex.submit(_work, row): row for row in pending}
                done = 0
                for fut in concurrent.futures.as_completed(futures):
                    result = fut.result()
                    results_lock_file.write(json.dumps(result) + "\n")
                    results_lock_file.flush()
                    done += 1
                    print(
                        f"[{done}/{len(pending)}] {status_label(result)} {result['duration_s']:.1f}s {result['id'][:12]}",
                        file=sys.stderr,
                    )
    finally:
        results_lock_file.close()

    print("\nGrading...", file=sys.stderr)
    scores = grade_predictions(predictions_path)
    scores_path = out_dir / "scores.json"
    scores_path.write_text(json.dumps(scores, indent=2))

    summary = {k: v for k, v in scores.items() if k != "per_task"}
    print(json.dumps(summary, indent=2))
    print(f"\nResults: {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
