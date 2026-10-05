"""One task, all four arms, side by side in the terminal.

  uv run python ultrafast/demo.py            # the local fixture
  uv run python ultrafast/demo.py --task hn

This is the showable version. bench.py is the same runs, repeated and
interleaved, which is what you quote numbers from.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
ARMS = ["lightpanda-jev", "lightpanda-chat", "chrome-jev", "chrome-chat"]
BLOCK, SHADE = "█", "░"


def colours(stream):
    if not stream.isatty():
        return dict.fromkeys(("bold", "dim", "reset", "green", "red", "cyan", "yellow"), "")
    return {"bold": "\033[1m", "dim": "\033[2m", "reset": "\033[0m", "green": "\033[32m",
            "red": "\033[31m", "cyan": "\033[36m", "yellow": "\033[33m"}


def bar(value, largest, width=16):
    filled = 0 if largest <= 0 else round(value / largest * width)
    filled = max(filled, 1) if value > 0 else 0
    return BLOCK * filled + SHADE * (width - filled)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default="hotel")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--timeout", type=int, default=400)
    args = parser.parse_args()
    c = colours(sys.stdout)

    print(f"\n{c['bold']}{c['cyan']}  ultrafast  ·  one structured decision per step, "
          f"on four combinations of browser and decider{c['reset']}")
    print(f"{c['dim']}  task     {args.task}{c['reset']}")
    print(f"{c['dim']}  browsers stock lightpanda (semantic tree) · headless Chrome "
          f"(browser-use snapshot.js){c['reset']}\n")

    results = []
    for arm in args.arms.split(","):
        browser, policy = arm.split("-")
        done = subprocess.run(
            [sys.executable, str(HERE / "run.py"), "--task", args.task,
             "--browser", browser, "--policy", policy],
            capture_output=True, text=True, timeout=args.timeout)
        try:
            results.append(json.loads(done.stdout.strip().splitlines()[-1]))
        except (ValueError, IndexError):
            results.append({"arm": arm, "ms": 0, "verified": False,
                            "error": (done.stderr or "no output").strip()[-120:]})

    slowest = max((r.get("ms") or 0) for r in results)
    for record in results:
        mark = (f"{c['green']}✓{c['reset']}" if record.get("verified")
                else f"{c['red']}✗{c['reset']}")
        seconds = (record.get("ms") or 0) / 1000
        print(f"  {c['cyan']}{record['arm']:<16}{c['reset']}{bar(record.get('ms') or 0, slowest)}"
              f"{seconds:>7.1f}s  {mark}")
        if record.get("error"):
            print(f"  {c['dim']}{'':<16}{record['error'][:96]}{c['reset']}")
            continue
        print(f"  {c['dim']}{'':<16}{record['decisions']:>2} decisions at "
              f"{record['decide_ms_median']:>4} ms median · "
              f"{record['actions']} actions · {record['browser_calls']} browser calls · "
              f"{record['elements_median']} elements offered{c['reset']}")
        print(f"  {c['dim']}{'':<16}${record['cost_usd']:.5f} at list price · "
              f"{record['peak_rss_mb']:,.0f} MB peak RSS{c['reset']}\n")

    good = [r for r in results if r.get("verified")]
    if len(good) > 1:
        fastest, slowest_ok = min(good, key=lambda r: r["ms"]), max(good, key=lambda r: r["ms"])
        print(f"  {c['dim']}{'-' * 74}{c['reset']}")
        ratio = (slowest_ok["peak_rss_mb"] or 1) / (fastest["peak_rss_mb"] or 1)
        memory = ""
        if ratio >= 1.5:
            memory = f", at {c['yellow']}{ratio:.0f}x{c['reset']} less memory"
        elif ratio <= 0.67:
            memory = f", though on {c['yellow']}{1 / ratio:.0f}x{c['reset']} the memory"
        print(f"  {c['bold']}{fastest['arm']}{c['reset']} finished in "
              f"{c['bold']}{fastest['ms'] / 1000:.1f}s{c['reset']}, "
              f"{c['yellow']}{slowest_ok['ms'] / fastest['ms']:.1f}x{c['reset']} faster than "
              f"{slowest_ok['arm']}{memory}.")
    print(f"  {c['dim']}A run counts only when a checker that never reads the model's DONE "
          f"passes.{c['reset']}\n")


if __name__ == "__main__":
    main()
