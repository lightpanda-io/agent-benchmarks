"""One measured run: one browser, one decider, one task.

  uv run python run.py --task hotel --browser lightpanda --policy jev -v
"""

import argparse
import contextlib
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import psutil
from tasks import TASKS
from ultrafast.agent import Agent

from ultrafast import net, table


def peak_rss(stop, sample):
    """Peak RSS over this process and everything it spawned, sampled at 50 ms.
    The Lightpanda sidecar and Chrome are both children, so both are counted."""
    me = psutil.Process()
    while not stop.is_set():
        total = 0
        for process in [me, *me.children(recursive=True)]:
            with contextlib.suppress(psutil.Error):
                total += process.memory_info().rss
        sample[0] = max(sample[0], total)
        time.sleep(0.05)


def build_browser(kind, url, binary, args):
    if kind == "lightpanda":
        from ultrafast.lp import Lightpanda
        return Lightpanda(url, binary=binary, args=args)
    from ultrafast.chrome import Chrome
    return Chrome(url, binary=binary)


def build_policy(kind, shape):
    if kind == "jev":
        from ultrafast.policy_jev import Jev
        return Jev(shape)
    from ultrafast.policy_chat import Chat
    return Chat()


def sha256(path):
    if not path or not Path(path).exists():
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, choices=sorted(TASKS))
    parser.add_argument("--browser", required=True, choices=("lightpanda", "chrome"))
    parser.add_argument("--policy", required=True, choices=("jev", "chat"))
    parser.add_argument("--request-shape", default="described",
                        choices=("described", "bare", "bare-trim"),
                        help="how much of the element table the Jev request repeats")
    parser.add_argument("--binary", default=None, help="browser binary; else LIGHTPANDA_BIN / PATH")
    parser.add_argument("--lpd-args", default="", help="extra flags for the lightpanda sidecar")
    parser.add_argument("--output", default=None, help="write the full state JSON here")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--record", default=None, help="write a replayable trace here")
    args = parser.parse_args(argv)

    task = TASKS[args.task]
    server = None
    if task.fixture:
        from fixture.serve import serve
        server, base = serve(0)
        url = base + "/"
    else:
        url = task.url
    context = task.setup() if task.setup else {}

    binary = args.binary
    if not binary and args.browser == "lightpanda":
        binary = os.environ.get("LIGHTPANDA_BIN")
    stop, sample = threading.Event(), [0]
    threading.Thread(target=peak_rss, args=(stop, sample), daemon=True).start()

    net.reset()
    policy = build_policy(args.policy, args.request_shape)
    browser = build_browser(args.browser, url, binary, args.lpd_args.split())
    record = {}
    try:
        agent = Agent(browser, policy, task.instruction(context),
                      verbose=args.verbose, record=bool(args.record))
        # Browser launch, the initial navigation and the first observation are
        # setup: they are outside the clock, so their calls are outside the
        # count too. Chrome's readyState poll alone is dozens of them.
        browser.calls, browser.call_ms = 0, 0.0
        for _ in agent.run():
            pass
        # A fresh, unclipped read for the checker, outside the clock and after
        # the loop has stopped -- never the observation the model decided on.
        final = browser.document()
        record = {
            "task": args.task,
            "browser": args.browser,
            "policy": args.policy,
            "request_shape": args.request_shape,
            "arm": f"{args.browser}-{args.policy}",
            **agent.totals(),
            "verification": task.verify(final, context),
            "cost_usd": round(policy.cost(agent.totals()["input_tokens"],
                                          agent.totals()["output_tokens"]), 6),
            "decider_model": policy.model,
            "final_url": final.url,
            "context": context,
            "peak_rss_mb": round(sample[0] / 1e6, 1),
            "provider_retries": net.STATS["retries"],
            "provider_retry_ms": round(net.STATS["retry_ms"]),
            "request_shrinks": net.STATS["shrinks"],
            "min_request_scale": round(net.STATS["min_scale"], 2),
            "max_offered": table.MAX_OFFERED,
            "binary_sha256": sha256(binary),
            "history": agent.history,
            "decision_log": agent.decisions,
            "text_call_log": agent.text_calls,
        }
    finally:
        stop.set()
        browser.close()
        if server:
            server.shutdown()

    record["verified"] = record.get("verification", {}).get("passed", False)
    if args.record:
        Path(args.record).write_text(json.dumps({
            "task": args.task, "arm": record["arm"], "goal": task.instruction(context),
            "browser": args.browser, "policy": args.policy,
            "decider_model": record["decider_model"], "ms": record["ms"],
            "verified": record["verified"], "verification": record["verification"],
            "final_url": record["final_url"], "peak_rss_mb": record["peak_rss_mb"],
            "browser_calls": record["browser_calls"], "input_tokens": record["input_tokens"],
            "provider_retry_ms": record["provider_retry_ms"],
            "steps": agent.trace,
        }, indent=1))
    if args.output:
        Path(args.output).write_text(json.dumps(record, indent=2))
    summary = {k: record[k] for k in (
        "arm", "task", "request_shape", "ms", "status", "verified", "decisions", "actions",
        "decide_ms_median", "browser_calls", "elements_median", "elements_max",
        "input_tokens", "cost_usd", "peak_rss_mb", "provider_retries",
        "provider_retry_ms", "request_shrinks", "min_request_scale", "error",
        "verification", "final_url")}
    print(json.dumps(summary), flush=True)
    return 0 if record["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
