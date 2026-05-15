"""WebBench LLM-judge CLI.

Thin wrapper over `agent_benchmarks.llm_judge`. The judge core (prompt,
provider dispatch, scoring, per-site breakdown) lives in `llm_judge.py`.

Variant pinned to `"webbench-text-only"` so WebBench scores.json files
record the protocol they were graded under.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..llm_judge import DEFAULT_JUDGE_MODEL, grade_predictions

VARIANT = "webbench-text-only"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("predictions", type=Path, help="Path to predictions.jsonl")
    parser.add_argument(
        "--judge-model",
        default=DEFAULT_JUDGE_MODEL,
        help=(
            f"Model to use as the judge (default: {DEFAULT_JUDGE_MODEL}). "
            "Supported prefixes: claude-* (Anthropic), gemini-* (Google)."
        ),
    )
    parser.add_argument(
        "--judge-workers",
        type=int,
        default=4,
        help="Parallel judge calls (default: 4)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write scores JSON here (default: predictions sibling scores.json)",
    )
    args = parser.parse_args(argv)

    result = grade_predictions(
        args.predictions,
        judge_model=args.judge_model,
        workers=args.judge_workers,
        variant=VARIANT,
    )
    out_path = args.out or args.predictions.parent / "scores.json"
    out_path.write_text(json.dumps(result, indent=2))

    summary = {k: v for k, v in result.items() if k != "per_task"}
    print(json.dumps(summary, indent=2))
    print(f"\nFull report written to: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
