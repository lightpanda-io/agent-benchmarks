"""Aggregate raw.jsonl files into summary tables (median / IQR / min / max).

Parallel datasets (bench.py --parallel N) are keyed by their batch size and
topology; `ms` there is the batch wall clock. A batch counts when at least
half its flows passed the content gate (a live site rejecting a few of N
simultaneous requests is a property of the site, and the batch clock is
still the time the batch took); the `flows` column shows the pass rate. Runs carrying per-step timings
(puppeteer scripts' BENCH_STEPS) get a second table: per config, the median
over runs of the per-run total for each step label, with the occurrence count.

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
    flows = {}
    for r in rows:
        if r.get("warmup") or not batch_counts(r):
            continue
        mode = r["mode"]
        if r.get("parallel", 1) > 1:
            mode = f"{mode} x{r['parallel']} {r.get('topology', '')}".rstrip()
        key = (r["task"], mode, r["config"])
        groups.setdefault(key, []).append(r["ms"])
        if r.get("parallel", 1) > 1:
            f = flows.setdefault(key, [0, 0])
            f[0] += r.get("n_ok", 0)
            f[1] += r["parallel"]

    out = []
    for (task, mode, config), ms in sorted(groups.items()):
        ms.sort()
        q = statistics.quantiles(ms, n=4) if len(ms) >= 4 else [ms[0], statistics.median(ms), ms[-1]]
        out.append({
            "task": task, "mode": mode, "config": config, "n": len(ms),
            "median": statistics.median(ms), "p25": q[0], "p75": q[2],
            "min": ms[0], "max": ms[-1],
            "flows": "%d/%d" % tuple(flows[(task, mode, config)]) if (task, mode, config) in flows else "",
        })
    return out


def batch_counts(r):
    """Single-flow runs count when ok; a parallel batch when at least half of
    its flows passed (see the module docstring)."""
    n = r.get("parallel", 1)
    if n == 1:
        return bool(r.get("ok"))
    return r.get("n_ok", 0) * 2 >= n


def step_summary(rows):
    """{(task, mode, config): {label: (median of per-run totals, occurrences per run, runs)}}.
    Flows inside a parallel batch each count as a run."""
    per_run = {}
    for r in rows:
        if r.get("warmup") or not r.get("ok"):
            continue
        for flow in r.get("flows", [r]):
            if not flow.get("steps"):
                continue
            totals, counts = {}, {}
            for label, ms in flow["steps"]:
                totals[label] = totals.get(label, 0) + ms
                counts[label] = counts.get(label, 0) + 1
            key = (r["task"], r["mode"], r["config"])
            for label in totals:
                per_run.setdefault(key, {}).setdefault(label, []).append((totals[label], counts[label]))
    out = {}
    for key, labels in per_run.items():
        out[key] = {label: (statistics.median(t for t, _ in v), max(c for _, c in v), len(v))
                    for label, v in labels.items()}
    return out


def main():
    rows = load(sys.argv[1:])
    summary = summarize(rows)

    dropped = [r for r in rows if not batch_counts(r) and not r.get("warmup")]
    if dropped:
        print(f"! {len(dropped)} failed run(s) excluded:", file=sys.stderr)
        for r in dropped:
            print(f"  {r['config']} {r['mode']} rot={r.get('rotation')}: {r.get('error', '?')[:100]}",
                  file=sys.stderr)

    mode_w = max([5] + [len(s["mode"]) for s in summary])
    has_flows = any(s["flows"] for s in summary)
    hdr = f"{'task':10} {'mode':{mode_w}} {'config':24} {'n':>3} {'median':>8} {'p25':>8} {'p75':>8} {'min':>8} {'max':>8}" \
        + (f" {'flows':>8}" if has_flows else "")
    print(hdr)
    print("-" * len(hdr))
    for s in summary:
        print(f"{s['task']:10} {s['mode']:{mode_w}} {s['config']:24} {s['n']:>3} "
              f"{s['median']:>8.0f} {s['p25']:>8.0f} {s['p75']:>8.0f} {s['min']:>8.0f} {s['max']:>8.0f}"
              + (f" {s['flows']:>8}" if has_flows else ""))

    steps = step_summary(rows)
    if steps:
        print("\nper-step median ms (per-run totals; xN = occurrences per run):")
        for (task, mode, config), labels in sorted(steps.items()):
            print(f"  {task} {mode} {config}")
            for label, (median, count, n) in labels.items():
                occ = f" x{count}" if count > 1 else ""
                print(f"    {label + occ:28} {median:>8.0f}   (n={n})")

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
