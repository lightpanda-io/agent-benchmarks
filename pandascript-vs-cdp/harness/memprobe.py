"""Peak-memory probe: PSS summed over every process a configuration needs.

For each config the task runs cold while a sampler walks /proc by session id
(everything the harness launches uses start_new_session, so each browser's
whole descendant forest — Chrome's browser/gpu/renderer processes included —
shares one session) and sums smaps_rollup Pss at ~25 ms intervals (a walk
costs ~4 ms; PandaScript flows finish in 30-90 ms, so anything slower
under-samples their peak). Reported
number = peak of that sum across the run. PSS rather than RSS so shared pages
aren't double-counted across a process tree.

CPU time comes from two sources. The script process is the harness's own
child, so its utime+stime (plus anything it reaped) is exact from rusage —
this covers all of pandascript and lightpanda-py, which run everything under
that one process. The engine's process tree is killed, not reaped, so the
same /proc walk records utime+stime per pid and sums the last value seen for
each (a process that exits between two samples loses at most the ~25 ms it
ran since the previous one; a final sample is taken before anything is
killed). Reported as `cpu_s` (everything) and, for CDP configs,
`cpu_engine_s` / `cpu_driver_s`. CPU per page is what turns into throughput
under parallel load, which wall clock on a single flow never shows.

Batches (--parallel N, --topology process|shared, as bench.py): the N flows
run at once and the sums span all of them, so peak_pss_mb / cpu_s are what
the whole batch costs — N PandaScript processes against one Chrome with N
renderers (shared) or N Chromes (process).

Usage:
  LPD_PATH=... uv run python harness/memprobe.py --tasks scrape,news,login_fx --iters 5
"""

import argparse
import datetime
import json
import os
import pathlib
import resource
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import browsers
from bench import (CONFIGS, FIXTURE_PORT, TASK_TIMEOUT_S, browseruse_dirs, browseruse_env,
                   browseruse_wipe, bu_tag, drop_unsupported_shared, kill_browseruse_daemon,
                   lpd_cache_flags, script_path, slot_port, stagger_for, validate, STAGGER_S)

ROOT = pathlib.Path(__file__).parent.parent
SCRATCH = pathlib.Path(os.environ.get("BENCH_SCRATCH", "/tmp")) / "pandascript-vs-cdp"


def session_of(pid):
    try:
        stat = pathlib.Path(f"/proc/{pid}/stat").read_text()
        return int(stat.rsplit(")", 1)[1].split()[3])  # field 6: session id
    except (OSError, IndexError, ValueError):
        return None


CLK_TCK = os.sysconf("SC_CLK_TCK")


def sample_sessions(sids, cpu_by_pid):
    """One /proc walk: returns the PSS sum (kB) over `sids` and records each
    member pid's cumulative CPU ticks and session in `cpu_by_pid`."""
    total = 0
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            stat = pathlib.Path(f"/proc/{entry.name}/stat").read_text()
            fields = stat.rsplit(")", 1)[1].split()
            sid = int(fields[3])  # field 6: session id
            if sid not in sids:
                continue
            ticks = int(fields[11]) + int(fields[12])  # fields 14/15: utime, stime
            cpu_by_pid[int(entry.name)] = (sid, ticks)
            for line in open(f"/proc/{entry.name}/smaps_rollup"):
                if line.startswith("Pss:"):
                    total += int(line.split()[1])  # kB
                    break
        except (OSError, IndexError, ValueError):
            continue
    return total


class PeakSampler(threading.Thread):
    def __init__(self, sids):
        super().__init__(daemon=True)
        self.sids = sids
        self.peak_kb = 0
        self.cpu_by_pid = {}
        self._halt = threading.Event()

    def _sample(self):
        self.peak_kb = max(self.peak_kb, sample_sessions(self.sids, self.cpu_by_pid))

    def run(self):
        while not self._halt.is_set():
            self._sample()
            time.sleep(0.025)

    def stop(self):
        self._halt.set()
        self.join()
        self._sample()

    def cpu_seconds(self, sids=None):
        return sum(t for sid, t in self.cpu_by_pid.values() if sids is None or sid in sids) / CLK_TCK


def run_config(cfg, task, lpd_path, chrome_path, fixture_env, lpd_flags=(), n=1, topology="process"):
    """One cold execution of `cfg`: n flows at once (n=1 is the single-flow
    probe). The PSS sum spans every engine and every script of the batch, so
    the peak is what the whole batch costs; CPU is likewise the batch total.
    process topology: one engine per flow (ports 100 apart, as bench.py);
    shared: one engine, every flow connects to it."""
    name, driver, engine, port = cfg
    base_env = {**os.environ, "LIGHTPANDA_DISABLE_TELEMETRY": "true", **fixture_env}
    owns_engine = driver not in ("pandascript", "lightpanda-py")
    browsers_ = []
    engine_sids = set()
    flows = []  # (cmd, env, stdin_text)

    def launch(slot_port_):
        if engine == "chrome":
            if driver == "browseruse":
                # Default-context CLI sees the profile's cache — keep the
                # cold-run protocol honest (see bench.run_once).
                shutil.rmtree(SCRATCH / f"chrome-mem-{slot_port_}", ignore_errors=True)
            b = browsers.launch_chrome(chrome_path, slot_port_, SCRATCH / f"chrome-mem-{slot_port_}")
        else:
            cap = ["--cdp-max-connections", str(n)] if topology == "shared" and n > 16 else []
            b = browsers.launch_lightpanda(lpd_path, slot_port_,
                                           [*lpd_cache_flags(f"mem-serve-{slot_port_}"), *lpd_flags, *cap])
        browsers_.append(b)
        engine_sids.add(session_of(b.proc.pid))
        return b

    try:
        return _run_batch(cfg, task, lpd_path, fixture_env, lpd_flags, n, topology, base_env,
                          owns_engine, browsers_, engine_sids, flows, launch)
    finally:
        # Reached on the normal path after everything was killed already; on
        # an exception it is what keeps a failed batch from leaking engines
        # (a leaked Chrome keeps its port, and the next launch there would
        # silently attach to it).
        if driver == "browseruse":
            for slot in range(n):
                kill_browseruse_daemon(bu_tag(name, slot))
        for b in browsers_:
            b.kill()


def _run_batch(cfg, task, lpd_path, fixture_env, lpd_flags, n, topology, base_env,
               owns_engine, browsers_, engine_sids, flows, launch):
    name, driver, engine, port = cfg
    shared = launch(port) if owns_engine and topology == "shared" else None
    for slot in range(n):
        env = dict(base_env)
        stdin_text = None
        tag = bu_tag(name, slot)
        if driver == "pandascript":
            cmd = [lpd_path, "agent", *lpd_cache_flags(f"mem-agent-{slot}"), *lpd_flags,
                   str(script_path(driver, task))]
        elif driver == "lightpanda-py":
            # Browser() spawns the mcp sidecar without a new session id, so the
            # script's session covers interpreter + sidecar for the PSS sum.
            env["LIGHTPANDA_BIN"] = lpd_path
            env["BENCH_LPD_ARGS"] = " ".join([*lpd_cache_flags(f"mem-lpd-py-{slot}"), *lpd_flags])
            cmd = [sys.executable, str(script_path(driver, task))]
        else:
            browser = shared if shared is not None else launch(slot_port(port, slot))
            if driver == "browseruse":
                browseruse_wipe(tag)
                env.update(browseruse_env(tag, browser.endpoint))
                cmd = [os.environ.get("BROWSER_USE_BIN", "browser-use")]
                stdin_text = script_path(driver, task).read_text()
            else:
                env["BROWSER_WS"] = browser.endpoint
                runner = sys.executable if driver == "playwright-py" else "node"
                cmd = [runner, str(script_path(driver, task))]
        flows.append((cmd, env, stdin_text))

    sampler = PeakSampler({s for s in engine_sids if s})
    ru0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    t0 = time.perf_counter()
    procs = []
    for slot, (cmd, env, stdin_text) in enumerate(flows):
        if stagger_for(name, slot):
            time.sleep(STAGGER_S)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                stdin=subprocess.PIPE if stdin_text is not None else None,
                                text=True, env=env, start_new_session=True)
        sampler.sids.add(session_of(proc.pid))
        procs.append(proc)
    sampler.start()
    if driver == "browseruse":
        # The CLI spawns its daemon with setsid, outside the sampled sessions;
        # fold each daemon's session in as soon as its pid file appears.
        def track_daemon(tag):
            pid_file = browseruse_dirs(tag)[1] / "bu.pid"
            deadline = time.time() + 15
            while time.time() < deadline:
                try:
                    sampler.sids.add(session_of(int(pid_file.read_text())))
                    return
                except (OSError, ValueError):
                    time.sleep(0.05)
        for slot in range(n):
            threading.Thread(target=track_daemon, args=(bu_tag(name, slot),), daemon=True).start()

    outputs = [None] * n

    def wait_one(i):
        proc = procs[i]
        try:
            outputs[i], _ = proc.communicate(input=flows[i][2], timeout=TASK_TIMEOUT_S[task])
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            outputs[i] = ""
    waiters = [threading.Thread(target=wait_one, args=(i,)) for i in range(n)]
    for w in waiters:
        w.start()
    for w in waiters:
        w.join()
    elapsed = time.perf_counter() - t0
    # Every script has been reaped: the rusage delta is their exact CPU
    # (nothing else of ours is waited for in between).
    ru1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    driver_cpu = (ru1.ru_utime - ru0.ru_utime) + (ru1.ru_stime - ru0.ru_stime)
    sampler.stop()
    if driver == "browseruse":
        for slot in range(n):
            kill_browseruse_daemon(bu_tag(name, slot))
    for b in browsers_:
        b.kill()

    errs = []
    for proc, out in zip(procs, outputs):
        errs.append(f"exit {proc.returncode}" if proc.returncode != 0 else validate(task, out))
    n_ok = sum(1 for e in errs if e is None)
    err = None if n_ok == n else next(e for e in errs if e)
    # Sampled CPU for every session but the scripts' own (the browseruse
    # daemons' sessions land here too: they are the CLI's processes holding
    # the CDP sockets, so they count as driver, not engine).
    script_sids = {session_of(proc.pid) for proc in procs} - {None}
    sampled_other = sampler.cpu_seconds(sampler.sids - script_sids)
    rec = {"config": name, "task": task, "peak_pss_mb": sampler.peak_kb / 1024,
           "elapsed_s": round(elapsed, 3), "cpu_s": round(driver_cpu + sampled_other, 3)}
    if engine_sids:
        rec["cpu_engine_s"] = round(sampler.cpu_seconds(engine_sids), 3)
        rec["cpu_driver_s"] = round(rec["cpu_s"] - rec["cpu_engine_s"], 3)
    if n > 1:
        rec["parallel"] = n
        rec["topology"] = topology
        rec["n_ok"] = n_ok
    rec["ok"] = err is None
    if err:
        rec["error"] = err
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default="scrape,news,login_fx")
    ap.add_argument("--iters", type=int, default=5)
    ap.add_argument("--pace", type=float, default=3.0)
    ap.add_argument("--configs", default=None, help="comma-separated subset of config names")
    ap.add_argument("--lpd-flags", default="", help="comma-separated extra flags for lightpanda agent")
    ap.add_argument("--parallel", type=int, default=1, help="flows per execution, run concurrently")
    ap.add_argument("--topology", choices=["process", "shared"], default="process")
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
            configs = drop_unsupported_shared(configs, args.parallel, args.topology)
            lpd_flags = tuple(f for f in args.lpd_flags.split(",") if f)
            for i in range(args.iters):
                for cfg in configs:
                    rec = run_config(cfg, task, lpd_path, chrome_path, {**fixture_env}, lpd_flags,
                                     args.parallel, args.topology)
                    rec["iter"] = i
                    raw.write(json.dumps(rec) + "\n")
                    raw.flush()
                    status = "ok" if rec["ok"] else f"FAIL ({rec.get('n_ok', 0)}/{args.parallel} ok, {rec.get('error','?')[:50]})"
                    print(f"[{task} {i+1}/{args.iters}] {rec['config']}: "
                          f"{rec['peak_pss_mb']:.0f} MB, {rec['cpu_s']:.2f} cpu-s {status}", flush=True)
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
