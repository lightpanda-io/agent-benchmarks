"""How many of these agents fit on one machine.

A single run's clock is ~91% model latency, so it cannot show a browser
difference. Concurrency can: the engine decides how much memory and CPU each
flow costs, and therefore how many flows a box will hold.

  uv run python ultrafast/fleet.py --ladder 1,4,8 --arms lightpanda-jev,chrome-jev
  uv run python ultrafast/fleet.py --ladder 16,32 --arms lightpanda-jev
"""

import argparse
import contextlib
import json
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil

HERE = Path(__file__).parent


def sample_tree(procs, stop, out, interval=0.1):
    """Peak RSS and total CPU across every flow and everything it spawned.

    Sampled from the parent rather than summing each flow's own figure, so a
    shared page cache or a shared parent is counted once.
    """
    peak, cpu_seen = 0, {}
    while not stop.is_set():
        total = 0
        for proc in list(procs):
            try:
                tree = [proc, *proc.children(recursive=True)]
            except psutil.Error:
                continue
            for p in tree:
                try:
                    total += p.memory_info().rss
                    times = p.cpu_times()
                    cpu_seen[p.pid] = times.user + times.system
                except psutil.Error:
                    pass
        peak = max(peak, total)
        time.sleep(interval)
    out["peak_rss_mb"] = round(peak / 1e6, 1)
    out["cpu_s"] = round(sum(cpu_seen.values()), 1)


def one_rung(arm, task, flows, timeout, binary):
    browser, policy = arm.split("-")
    command = [sys.executable, str(HERE / "run.py"), "--task", task,
               "--browser", browser, "--policy", policy]
    started = time.perf_counter()
    running = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True) for _ in range(flows)]
    tracked = []
    for proc in running:
        with contextlib.suppress(psutil.Error):
            tracked.append(psutil.Process(proc.pid))
    stop, sampled = threading.Event(), {}
    watcher = threading.Thread(target=sample_tree, args=(tracked, stop, sampled), daemon=True)
    watcher.start()

    results = []
    for proc in running:
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out = ""
        line = out.strip().splitlines()[-1] if out and out.strip() else ""
        try:
            results.append(json.loads(line))
        except ValueError:
            results.append({"verified": False, "status": "flow_lost", "ms": 0})
    wall = round((time.perf_counter() - started) * 1000)
    stop.set()
    watcher.join(timeout=3)

    finished = [r for r in results if r.get("verified")]
    times = sorted(r["ms"] for r in finished)
    return {
        "arm": arm, "flows": flows, "wall_ms": wall,
        "verified": len(finished), "attempted": flows,
        "median_flow_ms": times[len(times) // 2] if times else 0,
        "slowest_flow_ms": times[-1] if times else 0,
        "retries": sum(r.get("provider_retries", 0) for r in results),
        "binary": binary,
        **sampled,
        "flow_records": results,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default="hotel")
    parser.add_argument("--ladder", default="1,4,8")
    parser.add_argument("--arms", default="lightpanda-jev,chrome-jev")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--pace", type=float, default=8.0, help="seconds between rungs")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    folder = Path(args.out or HERE / "results" / f"fleet-{stamp}")
    folder.mkdir(parents=True, exist_ok=True)
    sink = (folder / "rungs.jsonl").open("a")

    available = psutil.virtual_memory().available / 1e9
    print(f"  machine: {psutil.cpu_count()} cores, {available:.1f} GB available\n", flush=True)

    for flows in [int(n) for n in args.ladder.split(",")]:
        for arm in args.arms.split(","):
            rung = one_rung(arm, args.task, flows, args.timeout, None)
            print(f"  {arm:<16} x{rung['flows']:<3} "
                  f"wall {rung['wall_ms'] / 1000:>6.1f}s  "
                  f"peak {rung.get('peak_rss_mb', 0):>8,.0f} MB  "
                  f"cpu {rung.get('cpu_s', 0):>6.1f}s  "
                  f"{rung['verified']}/{rung['attempted']} verified"
                  f"{'  ' + str(rung['retries']) + ' retries' if rung['retries'] else ''}",
                  flush=True)
            sink.write(json.dumps(rung) + "\n")
            sink.flush()
            time.sleep(args.pace)
    sink.close()
    print(f"\n{folder / 'rungs.jsonl'}", flush=True)


if __name__ == "__main__":
    main()
