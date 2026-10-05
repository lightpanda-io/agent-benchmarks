"""Round-robin campaign: every arm runs once per rotation, so live-site drift
hits all four equally.

  uv run python ultrafast/bench.py --tasks hotel,quotes,wikipedia,hn --runs 6
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).parent
ARMS = ["lightpanda-jev", "lightpanda-chat", "chrome-jev", "chrome-chat"]


def sha256(path):
    if not path or not Path(path).exists():
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def one(arm, task, timeout, extra):
    browser, policy = arm.split("-")
    command = [sys.executable, str(HERE / "run.py"), "--task", task,
               "--browser", browser, "--policy", policy, *extra]
    started = time.perf_counter()
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        line = done.stdout.strip().splitlines()[-1] if done.stdout.strip() else ""
        return json.loads(line)
    except (subprocess.TimeoutExpired, ValueError, IndexError, json.JSONDecodeError) as exc:
        return {"arm": arm, "task": task, "status": "harness_error", "verified": False,
                "ms": round((time.perf_counter() - started) * 1000),
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="hotel,quotes,wikipedia,hn")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--runs", type=int, default=6)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--pace", type=float, default=3.0, help="seconds between runs")
    parser.add_argument("--timeout", type=int, default=400)
    parser.add_argument("--out", default=None, help="results directory (default results/<stamp>)")
    parser.add_argument("--lpd-args", default="")
    args = parser.parse_args()

    tasks = args.tasks.split(",")
    arms = args.arms.split(",")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    folder = Path(args.out or HERE / "results" / stamp)
    folder.mkdir(parents=True, exist_ok=True)
    binary = os.environ.get("LIGHTPANDA_BIN")
    extra = ["--lpd-args", args.lpd_args] if args.lpd_args else []

    (folder / "meta.json").write_text(json.dumps({
        "started": stamp, "tasks": tasks, "arms": arms, "runs": args.runs,
        "warmup": args.warmup, "pace": args.pace,
        "lightpanda_binary": binary, "lightpanda_sha256": sha256(binary),
        "max_offered": os.environ.get("ULTRAFAST_MAX_OFFERED", "120"),
        "chat_model": os.environ.get("CHAT_MODEL", "google/gemini-3.8-flash"),
        "jev_model": os.environ.get("JEV_MODEL"),
        "text_model": os.environ.get("TEXT_MODEL", "inception/mercury-2.5"),
    }, indent=2))

    records = folder / "runs.jsonl"
    with records.open("a") as sink:
        for rotation in range(-args.warmup, args.runs):
            for task in tasks:
                for arm in arms:
                    record = one(arm, task, args.timeout, extra)
                    record["rotation"] = rotation
                    record["warmup"] = rotation < 0
                    mark = "ok " if record.get("verified") else "FAIL"
                    print(f"[{rotation:>3}] {task:<10} {arm:<16} {mark} "
                          f"{record.get('ms', 0):>7} ms  {record.get('status')}"
                          f"{'  ' + record['error'][:50] if record.get('error') else ''}",
                          flush=True)
                    sink.write(json.dumps(record) + "\n")
                    sink.flush()
                    time.sleep(args.pace)
    print(f"\n{records}", flush=True)


if __name__ == "__main__":
    main()
