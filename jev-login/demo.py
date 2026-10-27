"""`/login` decided by Jev, by a chat model, and by a recorded replay.

  uv run python jev-login/demo.py                  # the local HN-shaped fixture
  uv run python jev-login/demo.py --repeats 3
  uv run python jev-login/demo.py --site hn        # live HN, decide-only
  uv run python jev-login/demo.py --site hn --live # live HN, a real sign-in

One command, one page, one binary. What differs is who decides which field
takes the password: TypeSafe's Jev in one structured call, or the chat
model over a tree/nodeDetails/fill/click conversation. The branch under test
makes `/login` fall back to the chat prompt when TYPESAFE_API_KEY is unset,
so the arms are the same keystroke.
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
BIN = HERE / ".." / ".." / "browser" / "zig-out" / "bin" / "lightpanda"
FIXTURE = HERE / ".." / "pandascript-vs-cdp" / "harness" / "login_fixture.py"
FIXTURE_USER, FIXTURE_PASS = "bench_user", "bench_pass"
BLOCK, SHADE = "█", "░"

# The kitty keyboard-protocol push/pop the REPL emits around each prompt lands
# in a captured stdout too, so the result JSON needs digging out.
ANSI = re.compile(r"\x1b\[[0-9;<>?]*[a-zA-Z]")


def colours(stream):
    if not stream.isatty():
        return dict.fromkeys(("bold", "dim", "reset", "green", "red", "cyan", "yellow"), "")
    return {"bold": "\033[1m", "dim": "\033[2m", "reset": "\033[0m", "green": "\033[32m",
            "red": "\033[31m", "cyan": "\033[36m", "yellow": "\033[33m"}


def bar(value, largest, width=16):
    filled = 0 if largest <= 0 else min(round(value / largest * width), width)
    filled = max(filled, 1) if value > 0 else 0
    return BLOCK * filled + SHADE * (width - filled)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_fixture(port):
    proc = subprocess.Popen([sys.executable, str(FIXTURE), str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return proc
        except OSError:
            time.sleep(0.05)
    proc.kill()
    raise SystemExit(f"login fixture did not come up on port {port}")


def result_json(stdout):
    """The tool's own JSON, out of the REPL's terminal chatter."""
    clean = ANSI.sub("", stdout)
    start = clean.find("{")
    if start < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(clean[start:])[0]
    except ValueError:
        return None


def measure(trace_path):
    """Wall clock spent inside `/login`, and what it cost in calls.

    Every record carries `t`, the ms since the run began, written as the step
    ended; a tool record also carries its own `ms`. So the command started at
    `t - ms` of the `login` call and ended with the last record of the run —
    which for the chat arm is several model calls later, since the tool itself
    returns in 1ms with "no API key" and the agent takes it from there.
    """
    records = [json.loads(line) for line in trace_path.read_text().splitlines() if line]
    first = next((i for i, r in enumerate(records) if r.get("name") == "login"), None)
    if first is None:
        return None
    login, during = records[first], records[first:]
    end = next((r for r in records if r["type"] == "end"), records[-1])
    tokens = end.get("tokens", {})
    pages = [r["page"]["url"] for r in records if r.get("page")]
    return {
        "ms": records[-1]["t"] - (login["t"] - login["ms"]),
        "model_calls": end.get("model_calls", 0),
        # The opening `goto` is setup, not part of the command under test.
        "tool_calls": sum(1 for r in during if r["type"] == "tool"),
        "url": pages[-1] if pages else None,
        # Input tokens the chat side actually paid for, cache-adjusted.
        "prompt": (tokens.get("input", 0) - tokens.get("cached", 0)
                   - tokens.get("cache_creation", 0)),
        "output": tokens.get("output", 0),
    }


def run_one(arm, site, extra, args, tmp):
    """Drive one `/login` through the REPL on piped stdin, and time it.

    `--task` sends its argument to the model as a prompt, so it cannot reach a
    slash command; the REPL reads piped stdin fine, which is the only way in.
    """
    env = dict(os.environ) | site["env"]
    cmd = [str(BIN), "agent", "--url", site["url"],
           "--verbosity", "medium", "--log-level", "err"]

    if arm == "chat":
        # The fallback only fires when the tool reports the key as missing.
        env.pop("TYPESAFE_API_KEY", None)
        cmd += ["--provider", args.provider, "--model", args.model]
    else:
        # No chat model at all on the Jev and replay arms — that is the point.
        cmd.append("--no-llm")
        if arm == "replay":
            env.pop("TYPESAFE_API_KEY", None)

    trace = tmp / f"{arm}.jsonl"
    cmd += ["--trace", str(trace)]
    # Placeholders, never values: the substitution happens inside the browser
    # and the result echoes the placeholder back.
    command = " ".join([f"/login password={site['password']}",
                        f"username={site['username']}", *extra])
    try:
        done = subprocess.run(cmd, input=f"{command}\n/quit\n", env=env, timeout=args.timeout,
                              capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return {"arm": arm, "error": f"timed out after {args.timeout}s"}

    outcome = result_json(done.stdout) or {}
    taken = measure(trace) if trace.exists() else None
    if taken is None:
        tail = ANSI.sub("", done.stderr).strip().splitlines()[-1:] or ["no output"]
        return {"arm": arm, "error": tail[0][:110]}
    left_login_page = taken["url"] not in (None, site["url"])
    decided = bool(outcome.get("usernameSelector") and outcome.get("passwordSelector"))
    return {"arm": arm, **taken, "outcome": outcome,
            "ok": decided if "decideOnly=true" in extra else left_login_page}


def replay_args(outcome):
    """The selectors the Jev run reported, as arguments that ask nothing."""
    keys = ("usernameSelector", "passwordSelector", "submitSelector")
    return [f"{k}='{outcome[k]}'" for k in keys if outcome.get(k)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", default="fixture", choices=["fixture", "hn"])
    parser.add_argument("--live", action="store_true",
                        help="on --site hn, really sign in instead of deciding only")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--provider", default="anthropic")
    parser.add_argument("--model", default="claude-haiku-5-5")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    c = colours(sys.stdout)

    if not BIN.exists():
        raise SystemExit(f"no binary at {BIN}; run `make build-dev` in browser/")

    # `results/` is ignored repo-wide, as in ultrafast/.
    tmp = HERE / "results"
    tmp.mkdir(exist_ok=True)
    fixture = None
    if args.site == "fixture":
        port = free_port()
        fixture = start_fixture(port)
        site = {"url": f"http://127.0.0.1:{port}/login",
                "env": {"LP_USERNAME": FIXTURE_USER, "LP_PASSWORD": FIXTURE_PASS},
                "username": "$LP_USERNAME", "password": "$LP_PASSWORD"}
    else:
        site = {"url": "https://news.ycombinator.com/login", "env": {},
                "username": "$LP_HN_USERNAME", "password": "$LP_HN_PASSWORD"}
        for var in ("LP_HN_USERNAME", "LP_HN_PASSWORD"):
            if not os.environ.get(var):
                raise SystemExit(f"--site hn needs {var} set")

    # Live HN signs in for real; HN rate-limits repeated logins per IP, so the
    # default only asks Jev which fields it would use and types nothing.
    decide_only = args.site == "hn" and not args.live
    extra = ["decideOnly=true"] if decide_only else []
    arms = ["jev"] if decide_only else ["jev", "chat", "replay"]

    print(f"\n{c['bold']}{c['cyan']}  lightpanda /login  ·  one structured decision "
          f"vs one chat conversation{c['reset']}")
    print(f"{c['dim']}  page     {site['url']}{c['reset']}")
    print(f"{c['dim']}  deciders TypeSafe Jev · {args.model} · recorded selectors "
          f"(no model){c['reset']}")
    if decide_only:
        print(f"{c['dim']}  mode     decide-only — nothing is typed, nothing is "
              f"submitted{c['reset']}")
    print()

    try:
        runs = {arm: [] for arm in arms}
        for _ in range(args.repeats):
            selectors = []
            for arm in arms:
                if arm == "replay" and not selectors:
                    continue
                row = run_one(arm, site, extra + (selectors if arm == "replay" else []), args, tmp)
                runs[arm].append(row)
                if arm == "jev" and row.get("outcome"):
                    selectors = replay_args(row["outcome"])
                report(row, c)
    finally:
        if fixture:
            fixture.terminate()

    summarise(runs, c)


def report(row, c):
    arm = row["arm"]
    if "error" in row:
        print(f"    {c['cyan']}{arm:<7}{c['reset']} {c['red']}✗{c['reset']} {row['error']}")
        return
    secs = row["ms"] / 1000
    mark = f"{c['green']}✓{c['reset']}" if row["ok"] else f"{c['red']}✗{c['reset']}"
    calls = row["outcome"].get("calls")
    decider = (f"{row['model_calls']} chat calls" if row["model_calls"]
               else f"{calls} jev call{'s' if calls != 1 else ''}" if calls
               else "no model call")
    steps = f"{row['tool_calls']} tool call" + ("s" if row["tool_calls"] != 1 else "")
    print(f"    {c['cyan']}{arm:<7}{c['reset']} {bar(secs, 20)} {secs:6.2f}s  "
          f"{decider:<14} {steps:<13} {mark}")


def summarise(runs, c):
    def median(values):
        ordered = sorted(values)
        if not ordered:
            return 0
        middle = len(ordered) // 2
        return (ordered[middle] if len(ordered) % 2
                else (ordered[middle - 1] + ordered[middle]) / 2)

    times = {arm: median([r["ms"] for r in rows if "ms" in r]) for arm, rows in runs.items()}
    tokens = sum(r.get("prompt", 0) + r.get("output", 0) for r in runs.get("chat", []))
    print(f"\n{c['dim']}  {'─' * 74}{c['reset']}")
    line = "  ".join(f"{c['bold']}{arm}{c['reset']} {ms / 1000:.2f}s"
                     for arm, ms in times.items() if ms)
    print(f"  {c['bold']}median{c['reset']}   {line}")
    if times.get("jev") and times.get("chat"):
        print(f"           {c['yellow']}{times['chat'] / times['jev']:.0f}x{c['reset']} faster "
              f"than the chat model, on the same command and the same page")
    if times.get("jev") and times.get("replay"):
        print(f"           {c['yellow']}{times['jev'] / times['replay']:.0f}x{c['reset']} again "
              f"once the selectors are recorded — that run asks no one")
    if tokens:
        print(f"{c['dim']}  the chat arm spent {tokens} tokens reaching the same two "
              f"fields; jev's are not reported by the tool{c['reset']}")
    print()


if __name__ == "__main__":
    main()
