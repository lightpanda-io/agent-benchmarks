"""Wait-level A/B: the same puppeteer task at a chosen `waitUntil`, across engines.

The main benchmark's scripts wait on `load`/`domcontentloaded`, so it never
exercises `networkidle0`/`networkidle2` — the wait most real puppeteer code
reaches for when no selector obviously signals readiness. This harness swaps the
goto wait level and rotates one execution of each engine per round, with warm
browsers held for the whole run (per-navigation wait latency is the subject, not
launch cost).

Legs are lightpanda binaries given as LPD_OLD / LPD_NEW plus headless Chrome, so
a wait-semantics change can be measured against its own baseline and an anchor.

Usage:
  LPD_OLD=<binary> LPD_NEW=<binary> uv run python harness/waitab.py \
      --tasks scrape,retail,news --wait networkidle0 --runs 8
"""

import argparse
import datetime
import json
import os
import pathlib
import re
import statistics
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import browsers
from bench import script_path, validate

ROOT = pathlib.Path(__file__).parent.parent
SCRATCH = pathlib.Path(os.environ.get("BENCH_SCRATCH", "/tmp")) / "pandascript-vs-cdp"
TIMEOUT_S = 180
PORTS = {"lpd-old": 9281, "lpd-new": 9282, "chrome": 9283}


def script_for(task, wait):
    """The committed puppeteer script with every goto pinned to `wait`.

    Rewrites both goto forms: bare `goto(url)` and an existing
    `goto(url, {waitUntil: "..."})`. Selector waits are left in place so output
    validation stays identical to the main campaign's.
    """
    lines = []
    for line in script_path("puppeteer", task).read_text().splitlines(keepends=True):
        if "page.goto(" in line:
            if "waitUntil" in line:
                line = re.sub(r'waitUntil: "[a-z0-9]+"', f'waitUntil: "{wait}"', line)
            else:
                line = re.sub(r"\);(\s*)$", rf', {{ waitUntil: "{wait}" }});\1', line)
        lines.append(line)
    src = "".join(lines)
    # Inside the repo, not SCRATCH: node resolves node_modules by walking up
    # from the script's own directory.
    out_dir = ROOT / ".waitab"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{task}-{wait}.js"
    out.write_text(src)
    if f'waitUntil: "{wait}"' not in src:
        sys.exit(f"rewrite produced no {wait} goto for {task}; check script shape")
    return out


def run_once(script, task, endpoint):
    env = {**os.environ, "BROWSER_WS": endpoint}
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(["node", str(script)], capture_output=True, text=True,
                              timeout=TIMEOUT_S, env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    ms = (time.perf_counter() - t0) * 1000
    err = f"exit {proc.returncode}: {proc.stderr[-160:]}" if proc.returncode else validate(task, proc.stdout)
    return {"ms": ms, "ok": err is None, **({"error": err} if err else {})}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default="scrape,retail,news")
    ap.add_argument("--wait", default="networkidle0")
    ap.add_argument("--runs", type=int, default=8)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--pace", type=float, default=2.0)
    ap.add_argument("--lpd-flags", default="--disable-subframes")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    old, new = os.environ.get("LPD_OLD"), os.environ.get("LPD_NEW")
    if not old or not new:
        sys.exit("LPD_OLD and LPD_NEW required")
    lpd_flags = [f for f in args.lpd_flags.split(",") if f]
    tasks = args.tasks.split(",")

    out_dir = pathlib.Path(args.out) if args.out else ROOT / "results" / "waitab"
    out_dir.mkdir(parents=True, exist_ok=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    scripts = {t: script_for(t, args.wait) for t in tasks}
    engines = {
        "lpd-old": browsers.launch_lightpanda(old, PORTS["lpd-old"], lpd_flags),
        "lpd-new": browsers.launch_lightpanda(new, PORTS["lpd-new"], lpd_flags),
        "chrome": browsers.launch_chrome(os.environ.get("CHROME_PATH", "google-chrome-stable"),
                                         PORTS["chrome"], SCRATCH / "chrome-waitab"),
    }
    raw = open(out_dir / "raw.jsonl", "a")
    try:
        for task in tasks:
            for rotation in range(args.warmup + args.runs):
                warmup = rotation < args.warmup
                for name, browser in engines.items():
                    rec = run_once(scripts[task], task, browser.endpoint)
                    rec.update({"engine": name, "task": task, "wait": args.wait,
                                "rotation": rotation, "warmup": warmup})
                    raw.write(json.dumps(rec) + "\n")
                    label = "warmup" if warmup else f"run {rotation - args.warmup + 1}/{args.runs}"
                    status = "ok" if rec["ok"] else f"FAIL ({rec.get('error', '?')[:60]})"
                    print(f"[{task} {label}] {name}: {rec.get('ms', 0):.0f} ms {status}", flush=True)
                    time.sleep(args.pace)
                raw.flush()
    finally:
        raw.close()
        for b in engines.values():
            b.kill()

    rows = [json.loads(l) for l in open(out_dir / "raw.jsonl")]
    print(f"\n=== medians at waitUntil={args.wait} ===")
    for task in tasks:
        cells = []
        for name in engines:
            v = [r["ms"] for r in rows if r["task"] == task and r["engine"] == name
                 and r["ok"] and not r["warmup"]]
            cells.append(f"{name} {statistics.median(v):.0f}ms (n={len(v)})" if v else f"{name} n/a")
        print(f"  {task:8s} " + "  ".join(cells))
    print(f"done: {out_dir}")


if __name__ == "__main__":
    main()
