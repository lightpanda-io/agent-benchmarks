"""Round-robin benchmark orchestrator: PandaScript replay vs Puppeteer/Playwright
over CDP against lightpanda serve and headless Chrome.

Cold mode: nothing running -> result JSON on stdout. For CDP configs the timer
covers browser launch + CDP-ready poll + the node script; the kill happens
outside the timer. For PandaScript one process does everything.

Warm mode: the browser is launched once per config before the phase and held;
each execution times a fresh `node script.js` (which still pays node startup
and CDP connect - that is what a new task against a browser pool costs).
PandaScript has no warm/cold split; its warm run is the same full command.

Usage:
  LPD_PATH=/path/to/release/lightpanda uv run python harness/bench.py \
      --task scrape --mode cold --runs 20 --warmup 2 --pace 3
"""

import argparse
import datetime
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import browsers

ROOT = pathlib.Path(__file__).parent.parent
SCRATCH = pathlib.Path(os.environ.get("BENCH_SCRATCH", "/tmp")) / "pandascript-vs-cdp"

CONFIGS = [
    # (name, driver, engine, base CDP port)
    ("pandascript", "pandascript", "lightpanda", None),
    ("puppeteer-lightpanda", "puppeteer", "lightpanda", 9231),
    ("puppeteer-chrome", "puppeteer", "chrome", 9232),
    ("playwright-lightpanda", "playwright", "lightpanda", 9233),
    ("playwright-chrome", "playwright", "chrome", 9234),
]

TASK_TIMEOUT_S = {"scrape": 180, "scrape_par": 180, "login": 120, "login_fx": 60, "retail": 180, "news": 180}

FIXTURE_PORT = 9280


def script_path(driver, task):
    name = {"scrape": "hn_scrape", "scrape_par": "hn_scrape_par",
            "login": "hn_login", "login_fx": "hn_login_fx", "retail": "retail", "news": "news"}[task]
    return ROOT / "scripts" / driver / f"{name}.js"


def validate(task, stdout):
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return "stdout is not JSON"
    if task in ("scrape", "scrape_par"):
        if not isinstance(data, list) or len(data) != 5:
            return f"expected 5 stories, got {data if not isinstance(data, list) else len(data)}"
        for s in data:
            if not s.get("title") or not isinstance(s.get("comments"), list) or len(s["comments"]) > 3:
                return f"bad story shape: {json.dumps(s)[:120]}"
    elif task in ("login", "login_fx"):
        if not isinstance(data.get("karma"), int):
            return f"karma is not an int: {stdout[:120]}"
    elif task == "retail":
        if not isinstance(data, list) or len(data) != 3:
            return f"expected 3 products, got {data if not isinstance(data, list) else len(data)}"
        for p in data:
            if not p.get("name") or not isinstance(p.get("price"), (int, float)) \
                    or not isinstance(p.get("sizesAvailable"), list):
                return f"bad product shape: {json.dumps(p)[:120]}"
    elif task == "news":
        if not isinstance(data, list) or len(data) != 3:
            return f"expected 3 articles, got {data if not isinstance(data, list) else len(data)}"
        for a in data:
            if not a.get("headline") or not isinstance(a.get("paragraphs"), list) or not a["paragraphs"]:
                return f"bad article shape: {json.dumps(a)[:120]}"
    return None


def lpd_cache_flags(tag):
    """Fresh --http-cache-dir per browser/process lifetime. Within-run caching
    only — the fair analogue of Chrome's always-on cache (stricter, in fact:
    Chrome's pre-created profile persists its cache across cold runs)."""
    if not os.environ.get("LPD_CACHE"):
        return []
    d = SCRATCH / f"lpd-cache-{tag}"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return ["--http-cache-dir", str(d)]


def launch_browser(engine, port, lpd_path, chrome_path):
    if engine == "chrome":
        return browsers.launch_chrome(chrome_path, port, SCRATCH / f"chrome-profile-{port}")
    return browsers.launch_lightpanda(lpd_path, port, lpd_cache_flags(f"serve-{port}"))


def run_once(cfg, task, mode, lpd_path, chrome_path, held_browsers):
    name, driver, engine, port = cfg
    timeout = TASK_TIMEOUT_S[task]
    env = {**os.environ, "LIGHTPANDA_DISABLE_TELEMETRY": "true"}
    if task == "login_fx":
        base = f"http://127.0.0.1:{FIXTURE_PORT}"
        env.update({"BASE_URL": base, "LP_BASE_URL": base,
                    "LP_HN_USERNAME": "bench_user", "LP_HN_PASSWORD": "bench_pass"})
    rec = {"config": name, "task": task, "mode": mode}

    if driver == "pandascript":
        cmd = [lpd_path, "agent", *lpd_cache_flags("agent"), str(script_path(driver, task))]
        t0 = time.perf_counter()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        rec["ms"] = (time.perf_counter() - t0) * 1000
    else:
        cmd = ["node", str(script_path(driver, task))]
        browser = None
        try:
            if mode == "cold":
                t0 = time.perf_counter()
                browser = launch_browser(engine, port, lpd_path, chrome_path)
                rec["launch_ms"] = browser.ready_ms
            else:
                browser = held_browsers[name]
                t0 = time.perf_counter()
            env["BROWSER_WS"] = browser.endpoint
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
            rec["ms"] = (time.perf_counter() - t0) * 1000
        finally:
            if mode == "cold" and browser is not None:
                browser.kill()

    rec["exit"] = proc.returncode
    err = None
    if proc.returncode != 0:
        err = f"exit {proc.returncode}: {proc.stderr[-300:]}"
    else:
        err = validate(task, proc.stdout)
    rec["ok"] = err is None
    if err:
        rec["error"] = err
    if "captcha" in (proc.stderr or "") or "Validation required" in (proc.stdout or ""):
        rec["captcha"] = True
    return rec


def collect_meta(lpd_path, chrome_path, args):
    def out(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip()
        except Exception as e:
            return f"error: {e}"

    def sysfs(path):
        try:
            return open(path).read().strip()
        except OSError:
            return "?"

    governor = sysfs("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    epp = sysfs("/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference")
    platform_profile = sysfs("/sys/firmware/acpi/platform_profile")

    npm_versions = {}
    for pkg in ("puppeteer-core", "playwright-core"):
        pj = ROOT / "node_modules" / pkg / "package.json"
        if pj.exists():
            npm_versions[pkg] = json.loads(pj.read_text())["version"]

    return {
        "args": vars(args),
        "lpd_cache": bool(os.environ.get("LPD_CACHE")),
        "lightpanda": out([lpd_path, "--version"]),
        "chrome": out([chrome_path, "--version"]),
        "node": out(["node", "--version"]),
        "npm_deps": npm_versions,
        "kernel": platform.release(),
        "cpu_governor": governor,
        "cpu_epp": epp,
        "platform_profile": platform_profile,
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=["scrape", "scrape_par", "login", "login_fx", "retail", "news"], required=True)
    ap.add_argument("--mode", choices=["cold", "warm"], required=True)
    ap.add_argument("--runs", type=int, default=20, help="measured rotations")
    ap.add_argument("--warmup", type=int, default=2, help="unmeasured warmup rotations")
    ap.add_argument("--pace", type=float, default=3.0, help="seconds between executions")
    ap.add_argument("--configs", default=None, help="comma-separated subset of config names")
    ap.add_argument("--out", default=None, help="results dir (default: results/<UTC ts>)")
    args = ap.parse_args()

    lpd_path = os.environ.get("LPD_PATH")
    if not lpd_path:
        sys.exit("LPD_PATH must point at a ReleaseFast lightpanda binary (make build)")
    chrome_path = os.environ.get("CHROME_PATH", "google-chrome-stable")

    if args.task == "login" and not (os.environ.get("LP_HN_USERNAME") and os.environ.get("LP_HN_PASSWORD")):
        sys.exit("login task needs LP_HN_USERNAME / LP_HN_PASSWORD")

    configs = CONFIGS
    if args.configs:
        wanted = set(args.configs.split(","))
        configs = [c for c in CONFIGS if c[0] in wanted]
    if args.task == "scrape_par":
        configs = [c for c in configs if c[0] == "pandascript"]

    out_dir = pathlib.Path(args.out) if args.out else ROOT / "results" / (
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{args.task}-{args.mode}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    (out_dir / "meta.json").write_text(json.dumps(collect_meta(lpd_path, chrome_path, args), indent=2))

    if any(c[2] == "chrome" for c in configs):
        browsers.prewarm_chrome_profile(chrome_path, 9250, SCRATCH / "chrome-profile-9232")

    fixture = None
    if args.task == "login_fx":
        fixture = subprocess.Popen(
            [sys.executable, str(pathlib.Path(__file__).parent / "login_fixture.py"), str(FIXTURE_PORT)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        time.sleep(0.5)

    held = {}
    if args.mode == "warm":
        for name, driver, engine, port in configs:
            if driver != "pandascript":
                held[name] = launch_browser(engine, port, lpd_path, chrome_path)

    raw = open(out_dir / "raw.jsonl", "a")
    consecutive_fails = {}
    try:
        total = args.warmup + args.runs
        for rotation in range(total):
            is_warmup = rotation < args.warmup
            for cfg in configs:
                try:
                    rec = run_once(cfg, args.task, args.mode, lpd_path, chrome_path, held)
                except subprocess.TimeoutExpired:
                    rec = {"config": cfg[0], "task": args.task, "mode": args.mode,
                           "ok": False, "error": "timeout"}
                rec["rotation"] = rotation
                rec["warmup"] = is_warmup
                rec["ts"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                raw.write(json.dumps(rec) + "\n")
                raw.flush()
                status = "ok" if rec["ok"] else f"FAIL ({rec.get('error', '?')[:80]})"
                label = "warmup" if is_warmup else f"run {rotation - args.warmup + 1}/{args.runs}"
                print(f"[{label}] {cfg[0]}: {rec.get('ms', 0):.0f} ms {status}", flush=True)
                if rec.get("captcha"):
                    sys.exit("ABORT: captcha detected — stopping to avoid poisoning the account/IP")
                # Site-block guard: isolated failures are live-site noise, but a
                # config failing repeatedly in a row means the site is refusing
                # us — stop rather than hammer through (see README).
                name = cfg[0]
                consecutive_fails[name] = 0 if rec["ok"] else consecutive_fails.get(name, 0) + 1
                if consecutive_fails[name] >= 3:
                    sys.exit(f"ABORT: {name} failed 3 consecutive rotations — likely site block")
                time.sleep(args.pace)
    finally:
        raw.close()
        for b in held.values():
            b.kill()
        if fixture is not None:
            import signal as _signal
            os.killpg(fixture.pid, _signal.SIGKILL)

    print(f"done: {out_dir}")


if __name__ == "__main__":
    main()
