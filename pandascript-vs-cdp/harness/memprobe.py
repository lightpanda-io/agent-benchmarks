"""Peak-memory probe: PSS summed over every process a configuration needs.

For each config the task runs cold while a sampler walks /proc by session id
(everything the harness launches uses start_new_session, so each browser's
whole descendant forest — Chrome's browser/gpu/renderer processes included —
shares one session) and sums smaps_rollup Pss at ~150 ms intervals. Reported
number = peak of that sum across the run. PSS rather than RSS so shared pages
aren't double-counted across a process tree.

Usage:
  LPD_PATH=... uv run python harness/memprobe.py --tasks scrape,news,login_fx --iters 5
"""

import argparse
import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import browsers
from bench import (CONFIGS, FIXTURE_PORT, TASK_TIMEOUT_S, browseruse_dirs, browseruse_env,
                   browseruse_wipe, kill_browseruse_daemon, lpd_cache_flags, script_path, validate)

ROOT = pathlib.Path(__file__).parent.parent
SCRATCH = pathlib.Path(os.environ.get("BENCH_SCRATCH", "/tmp")) / "pandascript-vs-cdp"


def session_of(pid):
    try:
        stat = pathlib.Path(f"/proc/{pid}/stat").read_text()
        return int(stat.rsplit(")", 1)[1].split()[3])  # field 6: session id
    except (OSError, IndexError, ValueError):
        return None


def pss_of_sessions(sids):
    total = 0
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        if session_of(entry.name) not in sids:
            continue
        try:
            for line in open(f"/proc/{entry.name}/smaps_rollup"):
                if line.startswith("Pss:"):
                    total += int(line.split()[1])  # kB
                    break
        except OSError:
            continue
    return total


class PeakSampler(threading.Thread):
    def __init__(self, sids):
        super().__init__(daemon=True)
        self.sids = sids
        self.peak_kb = 0
        self._halt = threading.Event()

    def run(self):
        while not self._halt.is_set():
            self.peak_kb = max(self.peak_kb, pss_of_sessions(self.sids))
            time.sleep(0.15)

    def stop(self):
        self._halt.set()
        self.join()
        self.peak_kb = max(self.peak_kb, pss_of_sessions(self.sids))


def run_config(cfg, task, lpd_path, chrome_path, fixture_env, lpd_flags=()):
    name, driver, engine, port = cfg
    env = {**os.environ, "LIGHTPANDA_DISABLE_TELEMETRY": "true", **fixture_env}
    browser = None
    stdin_text = None
    sids = set()

    if driver == "pandascript":
        cmd = [lpd_path, "agent", *lpd_cache_flags("mem-agent"), *lpd_flags,
               str(script_path(driver, task))]
    elif driver == "lightpanda-py":
        # Browser() spawns the mcp sidecar without a new session id, so the
        # script's session covers interpreter + sidecar for the PSS sum.
        env["LIGHTPANDA_BIN"] = lpd_path
        env["BENCH_LPD_ARGS"] = " ".join([*lpd_cache_flags("mem-lpd-py"), *lpd_flags])
        cmd = [sys.executable, str(script_path(driver, task))]
    else:
        if engine == "chrome":
            if driver == "browseruse":
                # Default-context CLI sees the profile's cache — keep the
                # cold-run protocol honest (see bench.run_once).
                shutil.rmtree(SCRATCH / f"chrome-mem-{port}", ignore_errors=True)
            browser = browsers.launch_chrome(chrome_path, port, SCRATCH / f"chrome-mem-{port}")
        else:
            browser = browsers.launch_lightpanda(lpd_path, port, [*lpd_cache_flags(f"mem-serve-{port}"), *lpd_flags])
        sids.add(session_of(browser.proc.pid))
        if driver == "browseruse":
            browseruse_wipe(name)
            env.update(browseruse_env(name, browser.endpoint))
            cmd = [os.environ.get("BROWSER_USE_BIN", "browser-use")]
            stdin_text = script_path(driver, task).read_text()
        else:
            env["BROWSER_WS"] = browser.endpoint
            runner = sys.executable if driver == "playwright-py" else "node"
            cmd = [runner, str(script_path(driver, task))]

    t0 = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            stdin=subprocess.PIPE if stdin_text is not None else None,
                            text=True, env=env, start_new_session=True)
    sids.add(session_of(proc.pid))
    sampler = PeakSampler({s for s in sids if s})
    sampler.start()
    if driver == "browseruse":
        # The CLI spawns its daemon with setsid, outside both sampled sessions;
        # fold its session in as soon as the pid file appears.
        def track_daemon():
            pid_file = browseruse_dirs(name)[1] / "bu.pid"
            deadline = time.time() + 15
            while time.time() < deadline:
                try:
                    sampler.sids.add(session_of(int(pid_file.read_text())))
                    return
                except (OSError, ValueError):
                    time.sleep(0.05)
        threading.Thread(target=track_daemon, daemon=True).start()
    try:
        stdout, _ = proc.communicate(input=stdin_text, timeout=TASK_TIMEOUT_S[task])
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout = ""
    elapsed = time.perf_counter() - t0
    sampler.stop()
    if driver == "browseruse":
        kill_browseruse_daemon(name)
    if browser is not None:
        browser.kill()

    err = f"exit {proc.returncode}" if proc.returncode != 0 else validate(task, stdout)
    return {"config": name, "task": task, "peak_pss_mb": sampler.peak_kb / 1024,
            "elapsed_s": round(elapsed, 3),
            "ok": err is None, **({"error": err} if err else {})}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default="scrape,news,login_fx")
    ap.add_argument("--iters", type=int, default=5)
    ap.add_argument("--pace", type=float, default=3.0)
    ap.add_argument("--configs", default=None, help="comma-separated subset of config names")
    ap.add_argument("--lpd-flags", default="", help="comma-separated extra flags for lightpanda agent")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    lpd_path = os.environ.get("LPD_PATH") or sys.exit("LPD_PATH required")
    chrome_path = os.environ.get("CHROME_PATH", "google-chrome-stable")
    tasks = args.tasks.split(",")

    out_dir = pathlib.Path(args.out) if args.out else ROOT / "results" / (
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-memory")
    out_dir.mkdir(parents=True, exist_ok=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    fixture = None
    raw = open(out_dir / "raw.jsonl", "a")
    try:
        for task in tasks:
            fixture_env = {}
            if task == "login_fx":
                base = f"http://127.0.0.1:{FIXTURE_PORT}"
                fixture_env = {"BASE_URL": base, "LP_BASE_URL": base,
                               "LP_HN_USERNAME": "bench_user", "LP_HN_PASSWORD": "bench_pass"}
                fixture = subprocess.Popen(
                    [sys.executable, str(pathlib.Path(__file__).parent / "login_fixture.py"),
                     str(FIXTURE_PORT)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                time.sleep(0.5)
            configs = CONFIGS if not args.configs else \
                [c for c in CONFIGS if c[0] in args.configs.split(",")]
            if any(c[1] == "browseruse" for c in configs) and not os.environ.get("BROWSER_USE_BIN"):
                sys.exit("browseruse configs need BROWSER_USE_BIN (see bench.py)")
            lpd_flags = tuple(f for f in args.lpd_flags.split(",") if f)
            for i in range(args.iters):
                for cfg in configs:
                    rec = run_config(cfg, task, lpd_path, chrome_path, {**fixture_env}, lpd_flags)
                    rec["iter"] = i
                    raw.write(json.dumps(rec) + "\n")
                    raw.flush()
                    status = "ok" if rec["ok"] else f"FAIL ({rec.get('error','?')[:50]})"
                    print(f"[{task} {i+1}/{args.iters}] {rec['config']}: "
                          f"{rec['peak_pss_mb']:.0f} MB {status}", flush=True)
                    time.sleep(args.pace)
            if fixture is not None:
                import signal
                os.killpg(fixture.pid, signal.SIGKILL)
                fixture = None
    finally:
        raw.close()

    print(f"done: {out_dir}")


if __name__ == "__main__":
    main()
