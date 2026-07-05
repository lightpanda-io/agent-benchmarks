"""A/B variants of the PandaScript retail task, round-robin interleaved.

Each variant = (binary, extra lightpanda CLI flags, env). One Chrome reference
column (puppeteer retail, warm browser held for the whole run) rides along in
every rotation for drift control.

Usage:
  LPD_PATH=<release binary> uv run python harness/ab.py --runs 8 --warmup 1 \
      --variants baseline,cache,conns,noiframes
"""

import argparse
import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import browsers
from bench import validate

ROOT = pathlib.Path(__file__).parent.parent
SCRATCH = pathlib.Path(os.environ.get("BENCH_SCRATCH", "/tmp")) / "pandascript-vs-cdp"
SCRIPT = ROOT / "scripts" / "pandascript" / "retail.js"
CHROME_SCRIPT = ROOT / "scripts" / "puppeteer" / "retail.js"
TIMEOUT_S = 180


def variant_defs(lpd_path):
    cache_dir = SCRATCH / "ab-cache"
    return {
        "baseline": (lpd_path, [], {}),
        "cache": (lpd_path, ["--http-cache-dir", "PER_RUN_DIR"], {}),
        "cache-warm": (lpd_path, ["--http-cache-dir", str(cache_dir)], {}),
        "conns": (lpd_path, ["--http-max-host-open", "12", "--http-max-concurrent", "64"], {}),
        "noiframes": (lpd_path, ["--disable-subframes"], {}),
        "combo": (lpd_path, ["COMBO_PLACEHOLDER"], {}),
        # binary variants: point at a differently-built binary, no extra flags
        "bin:modmap": (os.environ.get("LPD_MODMAP", ""), [], {}),
        "bin:pipewait": (os.environ.get("LPD_PIPEWAIT", ""), [], {}),
    }


def run_variant(name, binary, flags, extra_env, run_idx):
    env = {**os.environ, "LIGHTPANDA_DISABLE_TELEMETRY": "true", **extra_env}
    resolved = []
    for f in flags:
        if f == "PER_RUN_DIR":
            d = SCRATCH / f"ab-cache-run-{name}-{run_idx}"
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True)
            resolved.append(str(d))
        else:
            resolved.append(f)
    cmd = [binary, "agent", *resolved, str(SCRIPT)]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S, env=env)
    except subprocess.TimeoutExpired:
        return {"variant": name, "ok": False, "error": "timeout"}
    ms = (time.perf_counter() - t0) * 1000
    err = None
    if proc.returncode != 0:
        err = f"exit {proc.returncode}: {proc.stderr[-200:]}"
    else:
        err = validate("retail", proc.stdout)
    return {"variant": name, "ms": ms, "ok": err is None, **({"error": err} if err else {})}


def run_chrome_ref(browser):
    env = {**os.environ, "BROWSER_WS": browser.endpoint}
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(["node", str(CHROME_SCRIPT)], capture_output=True, text=True,
                              timeout=TIMEOUT_S, env=env)
    except subprocess.TimeoutExpired:
        return {"variant": "chrome-ref", "ok": False, "error": "timeout"}
    ms = (time.perf_counter() - t0) * 1000
    err = None if proc.returncode == 0 and validate("retail", proc.stdout) is None else "failed"
    return {"variant": "chrome-ref", "ms": ms, "ok": err is None, **({"error": err} if err else {})}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=8)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--pace", type=float, default=3.0)
    ap.add_argument("--variants", default="baseline,cache,conns,noiframes")
    ap.add_argument("--combo-flags", default="", help="comma-separated flags for the combo variant")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    lpd_path = os.environ.get("LPD_PATH")
    if not lpd_path:
        sys.exit("LPD_PATH required")
    defs = variant_defs(lpd_path)
    if args.combo_flags:
        defs["combo"] = (lpd_path, args.combo_flags.split(","), {})

    wanted = args.variants.split(",")
    for w in wanted:
        if w not in defs:
            sys.exit(f"unknown variant {w}")
        if not defs[w][0]:
            sys.exit(f"variant {w} has no binary (set LPD_MODMAP/LPD_PIPEWAIT)")

    out_dir = pathlib.Path(args.out) if args.out else ROOT / "results" / (
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-ab")
    out_dir.mkdir(parents=True, exist_ok=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    chrome = browsers.launch_chrome(os.environ.get("CHROME_PATH", "google-chrome-stable"),
                                    9240, SCRATCH / "chrome-ab-profile")
    raw = open(out_dir / "raw.jsonl", "a")
    try:
        total = args.warmup + args.runs
        for rotation in range(total):
            is_warmup = rotation < args.warmup
            recs = []
            for name in wanted:
                binary, flags, env = defs[name]
                recs.append(run_variant(name, binary, flags, env, rotation))
                time.sleep(args.pace)
            recs.append(run_chrome_ref(chrome))
            time.sleep(args.pace)
            for rec in recs:
                rec.update({"rotation": rotation, "warmup": is_warmup,
                            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat()})
                raw.write(json.dumps(rec) + "\n")
                label = "warmup" if is_warmup else f"run {rotation - args.warmup + 1}/{args.runs}"
                status = "ok" if rec["ok"] else f"FAIL ({rec.get('error','?')[:60]})"
                print(f"[{label}] {rec['variant']}: {rec.get('ms', 0):.0f} ms {status}", flush=True)
            raw.flush()
    finally:
        raw.close()
        chrome.kill()

    print(f"done: {out_dir}")


if __name__ == "__main__":
    main()
