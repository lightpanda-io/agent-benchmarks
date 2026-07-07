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
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import browsers
from bench import CONFIGS, FIXTURE_PORT, TASK_TIMEOUT_S, script_path, validate

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


def run_config(cfg, task, lpd_path, chrome_path, fixture_env):
    name, driver, engine, port = cfg
    env = {**os.environ, "LIGHTPANDA_DISABLE_TELEMETRY": "true", **fixture_env}
    browser = None
    sids = set()

    if driver == "pandascript":
        cmd = [lpd_path, "agent", str(script_path(driver, task))]
    else:
        if engine == "chrome":
            browser = browsers.launch_chrome(chrome_path, port, SCRATCH / f"chrome-mem-{port}")
        else:
            browser = browsers.launch_lightpanda(lpd_path, port)
        sids.add(session_of(browser.proc.pid))
        env["BROWSER_WS"] = browser.endpoint
        cmd = ["node", str(script_path(driver, task))]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, env=env, start_new_session=True)
    sids.add(session_of(proc.pid))
    sampler = PeakSampler({s for s in sids if s})
    sampler.start()
    try:
        stdout, _ = proc.communicate(timeout=TASK_TIMEOUT_S[task])
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout = ""
    sampler.stop()
    if browser is not None:
        browser.kill()

    err = f"exit {proc.returncode}" if proc.returncode != 0 else validate(task, stdout)
    return {"config": name, "task": task, "peak_pss_mb": sampler.peak_kb / 1024,
            "ok": err is None, **({"error": err} if err else {})}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default="scrape,news,login_fx")
    ap.add_argument("--iters", type=int, default=5)
    ap.add_argument("--pace", type=float, default=3.0)
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
            for i in range(args.iters):
                for cfg in CONFIGS:
                    rec = run_config(cfg, task, lpd_path, chrome_path, {**fixture_env})
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
