"""Aggregate raw.jsonl files into summary tables (median / IQR / min / max).

Usage: uv run python harness/report.py results/<dir> [results/<dir> ...]
"""

import json
import pathlib
import statistics
import sys


def load(dirs):
    rows = []
    for d in dirs:
        p = pathlib.Path(d) / "raw.jsonl"
        rows += [json.loads(line) for line in p.read_text().splitlines()]
    return rows


def summarize(rows):
    groups = {}
    for r in rows:
        if r.get("warmup") or not r.get("ok"):
            continue
        key = (r["task"], r["mode"], r["config"])
        groups.setdefault(key, []).append(r["ms"])

    out = []
    for (task, mode, config), ms in sorted(groups.items()):
        ms.sort()
        q = statistics.quantiles(ms, n=4) if len(ms) >= 4 else [ms[0], statistics.median(ms), ms[-1]]
        out.append({
            "task": task, "mode": mode, "config": config, "n": len(ms),
            "median": statistics.median(ms), "p25": q[0], "p75": q[2],
            "min": ms[0], "max": ms[-1],
        })
    return out


def main():
    rows = load(sys.argv[1:])
    summary = summarize(rows)

    dropped = [r for r in rows if not r.get("ok") and not r.get("warmup")]
    if dropped:
        print(f"! {len(dropped)} failed run(s) excluded:", file=sys.stderr)
        for r in dropped:
            print(f"  {r['config']} {r['mode']} rot={r.get('rotation')}: {r.get('error', '?')[:100]}",
                  file=sys.stderr)

    hdr = f"{'task':10} {'mode':5} {'config':24} {'n':>3} {'median':>8} {'p25':>8} {'p75':>8} {'min':>8} {'max':>8}"
    print(hdr)
    print("-" * len(hdr))
    for s in summary:
        print(f"{s['task']:10} {s['mode']:5} {s['config']:24} {s['n']:>3} "
              f"{s['median']:>8.0f} {s['p25']:>8.0f} {s['p75']:>8.0f} {s['min']:>8.0f} {s['max']:>8.0f}")

    launches = {}
    for r in rows:
        if r.get("ok") and not r.get("warmup") and "launch_ms" in r:
            launches.setdefault(r["config"], []).append(r["launch_ms"])
    if launches:
        print("\nbrowser launch-to-ready (median ms, cold runs):")
        for config, ms in sorted(launches.items()):
            print(f"  {config:24} {statistics.median(ms):>8.0f}")


if __name__ == "__main__":
    main()
