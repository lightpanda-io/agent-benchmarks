"""
AssistantBench metric port.

The upstream grader (Yoran et al., 2024; arxiv 2407.15711) was not released at
the time of writing. This module reimplements the scoring described in section
4.1 of the paper:

  - Strings: token-level F1 over normalized (lowercased, punctuation-stripped)
    whitespace-split tokens.
  - Numbers: relative-error score max(0, 1 - |p - g| / max(|g|, 1)); counted
    as strict-correct at relative error <= 0.05.
  - Lists: best-alignment F1 between prediction and gold elements, where each
    pair is scored by its own type rule.
  - Dicts: mean of per-key score, each key scored by its own type rule.
  - Empty prediction: 0 (unless gold is also empty → 1).

The per-task score is in [0, 1]. Aggregate outputs:

  accuracy_soft  = mean per-task score
  accuracy_strict = fraction with score >= 0.5
"""

from __future__ import annotations

import argparse
import json
import math
import re
import string
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from ..common import mean

_PUNCT_TABLE = str.maketrans("", "", string.punctuation)


def _normalize_tokens(text: str) -> list[str]:
    text = text.lower().translate(_PUNCT_TABLE)
    return text.split()


def _token_f1(pred: str, gold: str) -> float:
    p_toks = _normalize_tokens(pred)
    g_toks = _normalize_tokens(gold)
    if not p_toks and not g_toks:
        return 1.0
    if not p_toks or not g_toks:
        return 0.0
    p_counter = Counter(p_toks)
    g_counter = Counter(g_toks)
    overlap = sum((p_counter & g_counter).values())
    if overlap == 0:
        return 0.0
    precision = overlap / len(p_toks)
    recall = overlap / len(g_toks)
    return 2 * precision * recall / (precision + recall)


_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _parse_number(s: str) -> float | None:
    s = s.strip().replace(",", "").rstrip("%").rstrip("$")
    try:
        return float(s)
    except ValueError:
        pass
    m = _NUM_RE.search(s)
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            return None
    return None


def _score_number(pred: Any, gold: float) -> float:
    p = pred if isinstance(pred, (int, float)) else _parse_number(str(pred))
    if p is None:
        return 0.0
    if math.isclose(float(p), gold, rel_tol=1e-9, abs_tol=1e-9):
        return 1.0
    denom = max(abs(gold), 1.0)
    return max(0.0, 1.0 - abs(float(p) - gold) / denom)


def _try_json(s: str) -> Any:
    s = s.strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        pass
    if "\n" in s:
        lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
        parsed: list[Any] = []
        for ln in lines:
            try:
                parsed.append(json.loads(ln))
            except (json.JSONDecodeError, ValueError):
                return None
        return parsed if parsed else None
    return None


def _infer_type(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "list"
    if isinstance(value, dict):
        return "dict"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    if isinstance(value, str):
        parsed = _try_json(value)
        if parsed is not None and isinstance(parsed, (list, dict)):
            return "list" if isinstance(parsed, list) else "dict"
        # AssistantBench gold convention: newline-separated items → list.
        lines = [ln.strip() for ln in value.splitlines() if ln.strip()]
        if len(lines) >= 2:
            return "list"
        if _parse_number(value) is not None and any(ch.isdigit() for ch in value):
            letters = sum(1 for ch in value if ch.isalpha())
            digits = sum(1 for ch in value if ch.isdigit())
            if digits >= letters:
                return "number"
        return "string"
    return "string"


def _coerce(value: Any, kind: str) -> Any:
    if kind == "list":
        if isinstance(value, list):
            return value
        parsed = _try_json(value) if isinstance(value, str) else None
        if isinstance(parsed, list):
            return parsed
        if isinstance(value, str):
            return [ln.strip() for ln in value.splitlines() if ln.strip()]
        return [value]
    if kind == "dict":
        if isinstance(value, dict):
            return value
        parsed = _try_json(value) if isinstance(value, str) else None
        if isinstance(parsed, dict):
            return parsed
        return {}
    if kind == "number":
        if isinstance(value, (int, float)):
            return float(value)
        return _parse_number(str(value))
    return "" if value is None else str(value)


def score_pair(prediction: Any, gold: Any) -> float:
    """Score a single (prediction, gold) pair in [0, 1]."""
    if prediction is None or (isinstance(prediction, str) and not prediction.strip()):
        if gold is None or (isinstance(gold, str) and not gold.strip()):
            return 1.0
        return 0.0

    kind = _infer_type(gold)
    g = _coerce(gold, kind)
    p = _coerce(prediction, kind)

    if kind == "number":
        if g is None:
            return 0.0
        return _score_number(p, float(g))

    if kind == "list":
        if not g:
            return 1.0 if not p else 0.0
        if not p:
            return 0.0
        # Best-alignment F1: greedy match by highest pairwise score.
        scores = [[score_pair(pi, gi) for gi in g] for pi in p]
        used_p: set[int] = set()
        used_g: set[int] = set()
        pairs: list[float] = []
        flat = sorted(
            ((scores[i][j], i, j) for i in range(len(p)) for j in range(len(g))),
            key=lambda t: -t[0],
        )
        for s, i, j in flat:
            if s <= 0:
                break
            if i in used_p or j in used_g:
                continue
            used_p.add(i)
            used_g.add(j)
            pairs.append(s)
        matched = sum(pairs)
        precision = matched / len(p)
        recall = matched / len(g)
        if precision + recall == 0:
            return 0.0
        return 2 * precision * recall / (precision + recall)

    if kind == "dict":
        if not g:
            return 1.0 if not p else 0.0
        keys = list(g.keys())
        sub_scores = []
        for k in keys:
            sub_scores.append(score_pair(p.get(k), g.get(k)))
        return sum(sub_scores) / len(keys) if keys else 0.0

    return _token_f1(str(p), str(g))


def grade_predictions(predictions_path: Path) -> dict[str, Any]:
    tasks: list[dict[str, Any]] = []
    with predictions_path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                tasks.append(json.loads(line))

    scored: list[tuple[dict[str, Any], float]] = []
    for t in tasks:
        pred = t.get("prediction", "")
        gold = t.get("gold", "")
        s = score_pair(pred, gold)
        scored.append((t, s))

    n = len(scored)
    answered = sum(1 for t, _ in scored if (t.get("prediction") or "").strip())
    timeouts = sum(1 for t, _ in scored if t.get("timed_out"))
    durations = [t.get("duration_s", 0.0) for t, _ in scored]

    by_diff: dict[str, list[float]] = {}
    for t, s in scored:
        d = t.get("difficulty") or "unknown"
        by_diff.setdefault(d, []).append(s)

    return {
        "n_tasks": n,
        "n_answered": answered,
        "timeouts": timeouts,
        "accuracy_soft": mean(s for _, s in scored),
        "accuracy_strict": mean(1.0 if s >= 0.5 else 0.0 for _, s in scored),
        "by_difficulty": {
            k: {
                "n": len(v),
                "accuracy_soft": mean(v),
                "accuracy_strict": mean(1.0 if s >= 0.5 else 0.0 for s in v),
            }
            for k, v in sorted(by_diff.items())
        },
        "avg_duration_s": mean(durations),
        "per_task": [
            {
                "id": t.get("id"),
                "difficulty": t.get("difficulty"),
                "score": s,
                "timed_out": bool(t.get("timed_out")),
                "duration_s": t.get("duration_s"),
            }
            for t, s in scored
        ],
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
