"""
GAIA metric port.

GAIA (Mialon et al., 2023; arxiv 2311.12983) grades each task with a
normalized exact-match rubric. The paper's scorer (released as
`scorer.py` in `gaia-benchmark/GAIA`) implements:

  - Numbers: normalize commas / units / dollar signs, compare equal.
  - Strings: lowercase, strip articles and punctuation, compare equal.
  - Lists (comma-separated in the gold): per-element apply number or
    string rule; require element count to match.

This module reimplements that rubric. A task is strict-correct iff its
score is 1.0 — there is no partial credit in the GAIA metric. We also
expose `accuracy_soft` as a looser metric (number comparisons allow ≤1%
relative error) so drift in numeric confabulation is visible.

Aggregate outputs:

  accuracy        = fraction of tasks with score == 1.0 (strict)
  accuracy_soft   = fraction of tasks with score > 0 (forgiving numbers)
"""

from __future__ import annotations

import argparse
import json
import re
import string
import sys
from pathlib import Path
from typing import Any

from ..common import mean, read_run_manifest, summarize_usage

_ARTICLES = {"a", "an", "the"}
_PUNCT_TABLE = str.maketrans("", "", string.punctuation)


def _normalize_str(s: str) -> str:
    s = s.lower().strip()
    s = s.translate(_PUNCT_TABLE)
    tokens = [t for t in s.split() if t not in _ARTICLES]
    return " ".join(tokens)


def _parse_number(s: str) -> float | None:
    """Parse a prediction as a number after stripping commas, currency symbols,
    and a trailing percent. Returns None if the string still isn't a clean
    number — we deliberately do NOT fall back to a regex-extracted first match,
    because that would silently accept CoT scratchpad like `$: if P3 fires...`
    as the digit 3."""
    s = s.strip().replace(",", "").rstrip("%")
    s = s.lstrip("$€£¥")
    try:
        return float(s)
    except ValueError:
        return None


def _is_numeric_gold(gold: str) -> bool:
    """True iff the gold answer looks like a bare number (possibly with
    a unit or comma-formatting). Used to pick the scoring rule."""
    stripped = gold.strip().replace(",", "").rstrip("%").lstrip("$€£¥")
    try:
        float(stripped)
        return True
    except ValueError:
        return False


def _is_list_gold(gold: str) -> bool:
    """True iff the gold answer is a comma-separated list of two or more
    items. Single-item strings that happen to contain commas (e.g.
    proper names like "New York, NY") are not caught — GAIA's rubric
    leans on gold formatting, not heuristics, so callers own any
    ambiguity."""
    parts = [p.strip() for p in gold.split(",") if p.strip()]
    return len(parts) >= 2


def _score_number(pred: str, gold: str, soft: bool) -> float:
    p = _parse_number(pred)
    g = _parse_number(gold)
    if p is None or g is None:
        return 0.0
    if p == g:
        return 1.0
    if soft and abs(g) > 0 and abs(p - g) / abs(g) <= 0.01:
        return 0.5
    return 0.0


def _score_string(pred: str, gold: str) -> float:
    if _normalize_str(pred) == _normalize_str(gold):
        return 1.0
    # Tolerant fallback: alphanumeric-only, case-insensitive match. Covers
    # cases where the model drops separators (e.g. cipher-reconstruction,
    # anagram reveals, sentence-as-concatenated-word outputs). The paper's
    # rubric is described as "string match after normalization" — this
    # branch extends the normalization to also absorb whitespace and
    # punctuation differences.
    p = "".join(c.lower() for c in pred if c.isalnum())
    g = "".join(c.lower() for c in gold if c.isalnum())
    return 1.0 if p and p == g else 0.0


def _score_list(pred: str, gold: str, soft: bool) -> float:
    gold_parts = [p.strip() for p in gold.split(",") if p.strip()]
    pred_parts = [p.strip() for p in re.split(r"[,\n]", pred) if p.strip()]
    if len(pred_parts) != len(gold_parts):
        return 0.0
    # GAIA compares lists as sets after per-element rule.
    used: set[int] = set()
    for _gi, g_item in enumerate(gold_parts):
        matched = False
        for pi, p_item in enumerate(pred_parts):
            if pi in used:
                continue
            if _is_numeric_gold(g_item):
                s = _score_number(p_item, g_item, soft=soft)
            else:
                s = _score_string(p_item, g_item)
            if s >= 1.0:
                used.add(pi)
                matched = True
                break
        if not matched:
            return 0.0
    return 1.0


def score_pair(prediction: Any, gold: Any, *, soft: bool = False) -> float:
    """Score a single (prediction, gold) pair per the GAIA rubric."""
    pred = "" if prediction is None else str(prediction).strip()
    gold_s = "" if gold is None else str(gold).strip()

    if not gold_s:
        return 1.0 if not pred else 0.0
    if not pred:
        return 0.0

    if _is_list_gold(gold_s):
        return _score_list(pred, gold_s, soft=soft)
    if _is_numeric_gold(gold_s):
        return _score_number(pred, gold_s, soft=soft)
    return _score_string(pred, gold_s)


def grade_predictions(predictions_path: Path) -> dict[str, Any]:
    tasks: list[dict[str, Any]] = []
    with predictions_path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                tasks.append(json.loads(line))

    scored: list[tuple[dict[str, Any], float, float]] = []
    for t in tasks:
        pred = t.get("prediction", "")
        gold = t.get("gold", "")
        strict = score_pair(pred, gold)
        soft = max(strict, score_pair(pred, gold, soft=True))
        scored.append((t, strict, soft))

    n = len(scored)
    answered = sum(1 for t, _, _ in scored if (t.get("prediction") or "").strip())
    timeouts = sum(1 for t, _, _ in scored if t.get("timed_out"))
    durations = [t.get("duration_s", 0.0) for t, _, _ in scored]
    usage_summary = summarize_usage(t for t, _, _ in scored)

    by_level: dict[str, list[tuple[float, float]]] = {}
    per_task: list[dict[str, Any]] = []
    for t, s_strict, s_soft in scored:
        level = t.get("level") or t.get("Level")
        by_level.setdefault(str(level) if level is not None else "?", []).append((s_strict, s_soft))
        per_task.append(
            {
                "id": t.get("id"),
                "level": level,
                "score": s_strict,
                "score_soft": s_soft,
                "timed_out": bool(t.get("timed_out")),
                "duration_s": t.get("duration_s"),
                "usage": t.get("usage"),
            }
        )

    return {
        **read_run_manifest(predictions_path),
        "n_tasks": n,
        "n_answered": answered,
        "timeouts": timeouts,
        "accuracy": mean(s for _, s, _ in scored),
        "accuracy_soft": mean(s for _, _, s in scored),
        "by_level": {
            k: {
                "n": len(v),
                "accuracy": mean(s for s, _ in v),
                "accuracy_soft": mean(s for _, s in v),
            }
            for k, v in sorted(by_level.items())
        },
        "avg_duration_s": mean(durations),
        **usage_summary,
        "per_task": per_task,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("predictions", type=Path, help="Path to predictions.jsonl")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write scores JSON here (default: predictions sibling scores.json)",
    )
    args = parser.parse_args(argv)

    result = grade_predictions(args.predictions)
    out_path = args.out or args.predictions.parent / "scores.json"
    out_path.write_text(json.dumps(result, indent=2))

    summary = {k: v for k, v in result.items() if k != "per_task"}
    print(json.dumps(summary, indent=2))
    print(f"\nFull report written to: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
