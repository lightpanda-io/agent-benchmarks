"""Medians and IQR per arm, and the pass rate next to every one of them.

A run counts only when the loop finished cleanly AND the independent checker
passed. Failed runs still appear, as the pass rate.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

COLUMNS = [
    ("ms", "time", lambda v: f"{v / 1000:.1f}s"),
    # Retry backoff sleeps inside the provider call, so it lands in both the run
    # clock and the decision latency. This column is the run without it.
    ("ms_ex_retry", "-retry", lambda v: f"{v / 1000:.1f}s"),
    ("decide_ms_median", "decide", lambda v: f"{v:.0f}ms"),
    ("decisions", "calls", lambda v: f"{v:.0f}"),
    ("browser_calls", "browser", lambda v: f"{v:.0f}"),
    ("elements_median", "elems", lambda v: f"{v:.0f}"),
    ("input_tokens", "tok in", lambda v: f"{v:,.0f}"),
    ("cost_usd", "cost", lambda v: f"${v:.5f}"),
    ("peak_rss_mb", "rss", lambda v: f"{v:,.0f}MB"),
]
WIDTH = 9


def quantile(values, fraction):
    if not values:
        return 0
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results", help="a results directory or a runs.jsonl")
    parser.add_argument("--include-warmup", action="store_true")
    args = parser.parse_args()

    path = Path(args.results)
    if path.is_dir():
        path = path / "runs.jsonl"
    runs = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not args.include_warmup:
        runs = [r for r in runs if not r.get("warmup")]
    for run in runs:
        if run.get("ms") is not None:
            run["ms_ex_retry"] = run["ms"] - run.get("provider_retry_ms", 0)

    grouped = defaultdict(list)
    for run in runs:
        grouped[(run["task"], run["arm"])].append(run)

    meta = path.parent / "meta.json"
    if meta.exists():
        print(json.dumps(json.loads(meta.read_text()), indent=1), "\n")

    for task in dict.fromkeys(r["task"] for r in runs):
        print(f"== {task}")
        header = f"  {'arm':<17}{'pass':>6}"
        for _key, label, _format in COLUMNS:
            header += f"{label:>{WIDTH}}"
        print(header)
        for arm in dict.fromkeys(r["arm"] for r in runs):
            rows = grouped.get((task, arm), [])
            if not rows:
                continue
            good = [r for r in rows if r.get("verified") and r.get("status") == "done"]
            line = f"  {arm:<17}{f'{len(good)}/{len(rows)}':>6}"
            for key, _label, render in COLUMNS:
                values = [r[key] for r in good if r.get(key) is not None]
                line += f"{render(quantile(values, 0.5)) if values else '-':>{WIDTH}}"
            print(line)
            spread = [r["ms"] for r in good]
            if len(spread) > 1:
                print(f"  {'':<23}  IQR {quantile(spread, 0.25)/1000:.1f}-"
                      f"{quantile(spread, 0.75)/1000:.1f}s over {len(spread)} runs")
            retries = sum(r.get("provider_retries", 0) for r in rows)
            if retries:
                print(f"  {'':<23}  {retries} provider retries across {len(rows)} runs")
        print()


if __name__ == "__main__":
    main()
