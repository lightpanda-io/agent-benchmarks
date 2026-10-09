"""
Flag tasks where the agent went looking for the benchmark's own answers.

Some models check an answer by searching the question verbatim, find a copy
of GAIA or AssistantBench (Hugging Face datasets and Spaces, GitHub mirrors,
eval leaderboards, published agent logs) and read the gold answer from it.
This scans each task's trace for tool calls whose arguments point at such a
source, or contain one of the suite's task ids, and reports strict accuracy with those tasks
counted as wrong.

Only the agent's own actions are matched (search queries, URLs it opened or
fetched, scripts it ran), not tool outputs: a search result that merely
lists a mirror isn't a leak until the agent follows it. That makes the count
a lower bound, since an answer can also sit in a search snippet.

Needs a trace: the Lightpanda runners always record one, and the
agent-browser runners do for runs that end normally.

    uv run leak-audit results/gaia-flash38/native/<ts>/predictions.jsonl [...]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Hosts and paths that publish benchmark questions with answers, or agent
# logs of them. Extend as new mirrors turn up in traces.
ANSWER_SOURCES = re.compile(
    r"huggingface\.co/(api/)?(datasets|spaces)"
    r"|datasets-server\.huggingface\.co"
    r"|\.hf\.space"
    r"|hf\.co/(datasets|spaces)/"
    r"|harbor-datasets|harbor-framework"
    r"|evalscope"
    r"|leaderboard\."
    r"|paperswithcode"
    r"|Who_and_When"
    r"|GAIA-modified|gaia_subset|gaia-benchmark|gaia( |\+|%20)benchmark"
    r"|metadata\.jsonl"
    r"|assistantbench",
    re.IGNORECASE,
)

# URL patterns that `--block-answer-sources` refuses, in Lightpanda's
# `--block-urls` syntax: the whole URL, case-insensitive, `*` matches
# anything. agent-browser's routes accept the same patterns. Narrower than
# ANSWER_SOURCES where a broad match would block legitimate sources: all
# of Hugging Face's datasets and Spaces go, but not its models or docs. A
# search engine URL whose query names the benchmark is blocked too. The
# runners add one `*<task id>*` pattern per task of the suite on top.
BLOCKED_URL_PATTERNS = [
    "*huggingface.co/datasets/*",
    "*huggingface.co/spaces/*",
    "*hf.co/datasets/*",
    "*hf.co/spaces/*",
    "*datasets-server.huggingface.co*",
    "*huggingface.co/api/datasets*",
    "*huggingface.co/api/spaces*",
    # Spaces' own hosts: course Spaces serve GAIA questions and files.
    "*.hf.space*",
    "*gaia-benchmark*",
    "*assistantbench*",
    # "GAIA benchmark" as typed, in a URL query, or URL-encoded; plain
    # "gaia" would also block questions about the Gaia spacecraft.
    "*gaia benchmark*",
    "*gaia+benchmark*",
    "*gaia%20benchmark*",
    "*github*gaia*",
    "*harbor-datasets*",
    "*harbor-framework*",
    "*evalscope*",
    "*leaderboard.neurometric*",
    "*paperswithcode.com/dataset*",
    "*metadata.jsonl*",
]

# Score a task must reach to count as correct, per suite.
STRICT_THRESHOLD = {"gaia": 1.0, "assistantbench": 0.5}


def leak_hits(trace: list[dict[str, Any]], task_ids: set[str]) -> list[str]:
    """The tool-call arguments in `trace` that point at an answer source.

    `task_ids` are the suite's own ids (GAIA UUIDs, AssistantBench hashes).
    They only appear in mirrors of the benchmark, so searching for one is a
    lookup. A generic UUID pattern would also catch ordinary site URLs."""
    hits = []
    for call in trace or []:
        args = json.dumps(call.get("args"), ensure_ascii=False)
        if ANSWER_SOURCES.search(args) or any(t in args for t in task_ids):
            hits.append(f"{call.get('tool')}: {args[:200]}")
    return hits


def audit(predictions_path: Path) -> dict[str, Any]:
    scores_path = predictions_path.parent / "scores.json"
    scores = json.loads(scores_path.read_text())
    suite = "gaia" if "accuracy" in scores and "by_level" in scores else "assistantbench"
    threshold = STRICT_THRESHOLD[suite]
    score_by_id = {t["id"]: t["score"] for t in scores["per_task"]}

    rows = [json.loads(line) for line in predictions_path.read_text().splitlines() if line]
    n = len(rows)
    task_ids = {r["id"] for r in rows}
    with_trace = sum(1 for r in rows if r.get("trace"))
    correct = 0
    correct_clean = 0
    flagged = []
    for r in rows:
        ok = score_by_id.get(r["id"], 0.0) >= threshold
        hits = leak_hits(r.get("trace") or [], task_ids)
        correct += ok
        if hits:
            flagged.append({"id": r["id"], "correct": ok, "hits": hits[:5]})
        else:
            correct_clean += ok
    return {
        "suite": suite,
        "n_tasks": n,
        "n_with_trace": with_trace,
        "n_flagged": len(flagged),
        "n_flagged_correct": sum(1 for f in flagged if f["correct"]),
        "accuracy_strict": correct / n if n else 0.0,
        "accuracy_strict_leaks_wrong": correct_clean / n if n else 0.0,
        "flagged": flagged,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("predictions", type=Path, nargs="+", help="predictions.jsonl files")
    args = parser.parse_args(argv)

    for path in args.predictions:
        result = audit(path)
        (path.parent / "leaks.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(
            f"{path.parent}: {result['n_flagged']}/{result['n_tasks']} tasks touched answer "
            f"sources ({result['n_flagged_correct']} scored correct); strict "
            f"{result['accuracy_strict']:.1%} -> {result['accuracy_strict_leaks_wrong']:.1%} "
            f"with them counted wrong; traces on {result['n_with_trace']}/{result['n_tasks']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
