"""`/login` decided by Jev, by a chat model, and by a recorded replay. See README.md."""

import argparse
import http.server
import importlib.util
import json
import os
import statistics
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path

HERE = Path(__file__).parent
RESULTS = HERE / "results"
BIN = HERE / ".." / ".." / "browser" / "zig-out" / "bin" / "lightpanda"
FIXTURE = HERE / ".." / "pandascript-vs-cdp" / "harness" / "login_fixture.py"
FIXTURE_USER, FIXTURE_PASS = "bench_user", "bench_pass"
BLOCK, SHADE = "█", "░"
CODES = {"bold": "\033[1m", "dim": "\033[2m", "reset": "\033[0m", "green": "\033[32m",
         "red": "\033[31m", "cyan": "\033[36m", "yellow": "\033[33m"}


def colours(stream):
    return CODES if stream.isatty() else dict.fromkeys(CODES, "")


def bar(secs, width=16):
    """On a 20 s scale, where the chat arm usually lands; a slower run clamps."""
    filled = min(round(secs / 20 * width), width)
    filled = max(filled, 1) if secs > 0 else 0
    return BLOCK * filled + SHADE * (width - filled)


def count(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def start_fixture():
    """The HN-shaped fixture, served from this process on a free port."""
    spec = importlib.util.spec_from_file_location("login_fixture", FIXTURE)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), fixture.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def load_trace(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def login_window(records):
    """The trace from `/login` on, when it started, and how long it took in ms.

    Every record carries `t`, the ms since the run began, written as the step
    ended; a tool record also carries its own `ms`. So the command started at
    `t - ms` of the `login` call and ended with the last record of the run,
    which for the chat arm is several model calls later: the tool itself
    returns in 1ms with "no API key" and the agent takes it from there. The
    opening `goto` comes before it and is left out.
    """
    i = next((i for i, r in enumerate(records) if r.get("name") == "login"), None)
    if i is None:
        return None
    during = records[i:]
    start = during[0]["t"] - during[0]["ms"]
    return during, start, during[-1]["t"] - start


def last_page(records):
    return next((r["page"] for r in reversed(records) if r.get("page")), {})


def measure(records):
    window = login_window(records)
    if window is None:
        return None
    during, _, ms = window
    models = [r for r in during if r["type"] == "model"]
    try:
        # Needs --trace-results; the chat arm's `login` returns an error, not JSON.
        outcome = json.loads(during[0].get("result") or "")
    except ValueError:
        outcome = {}
    return {
        "ms": ms,
        "model_calls": len(models),
        "tool_calls": sum(1 for r in during if r["type"] == "tool"),
        # The page `/login` was run on: a record's `page` is the one it left.
        "login_page": last_page(records[:-len(during)]),
        "page": last_page(during),
        # Cache-adjusted: what the chat side paid for.
        "tokens": sum(m["tokens"]["input"] - m["tokens"]["cached"]
                      - m["tokens"]["cache_creation"] + m["tokens"]["output"] for m in models),
        "outcome": outcome,
    }


def run_one(arm, site, args, decide_only, selectors=()):
    """Drive one `/login` through the REPL on piped stdin, and time it.

    `--task` sends its argument to the model as a prompt, so it cannot reach a
    slash command; the REPL reads piped stdin fine, which is the only way in.
    """
    env = dict(os.environ) | site["env"]
    if arm != "jev":
        # The chat fallback fires on a missing key, and without one the replay
        # provably asks nothing.
        env.pop("TYPESAFE_API_KEY", None)
    chat = ["--provider", args.provider, "--model", args.model,
            *(["--base-url", args.base_url] if args.base_url else [])]
    decider = chat if arm == "chat" else ["--no-llm"]
    trace = RESULTS / f"{arm}.jsonl"
    cmd = [str(BIN), "agent", "--url", site["url"], "--verbosity", "medium", "--log-level", "err",
           *decider, "--trace", str(trace), "--trace-results"]
    # Placeholders, never values: the substitution happens inside the browser
    # and the result echoes the placeholder back.
    command = " ".join([f"/login password={site['password']}", f"username={site['username']}",
                        *(["decideOnly=true"] if decide_only else []), *selectors])
    try:
        done = subprocess.run(cmd, input=f"{command}\n/quit\n", env=env, timeout=args.timeout,
                              capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return {"arm": arm, "error": f"timed out after {args.timeout}s"}

    taken = measure(load_trace(trace)) if trace.exists() else None
    if taken is None:
        tail = done.stderr.strip().splitlines()[-1:] or ["no output"]
        return {"arm": arm, "error": tail[0][:110]}
    if decide_only:
        picked = taken["outcome"]
        ok = bool(picked.get("usernameSelector") and picked.get("passwordSelector"))
    else:
        ok = signed_in(taken["login_page"], taken["page"])
    return {"arm": arm, **taken, "ok": ok}


def signed_in(login, last):
    """Off the login page but still on its site: a model can also wander off to
    a URL it made up, which changes the URL just the same."""
    if not login.get("url") or not last.get("url"):
        return False
    host = urllib.parse.urlsplit(login["url"]).netloc
    return (urllib.parse.urlsplit(last["url"]).netloc == host and last["url"] != login["url"]
            and last.get("title") != login.get("title"))


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
    parser.add_argument("--base-url", help="an OpenAI-compatible endpoint, with --provider openai")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    c = colours(sys.stdout)

    if not BIN.exists():
        raise SystemExit(f"no binary at {BIN}; run `make build-dev` in browser/")
    RESULTS.mkdir(exist_ok=True)

    fixture = None
    if args.site == "fixture":
        fixture = start_fixture()
        site = {"url": f"http://127.0.0.1:{fixture.server_address[1]}/login",
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

    print(f"\n{c['bold']}{c['cyan']}  lightpanda /login  ·  one structured decision "
          f"vs one chat conversation{c['reset']}")
    print(f"{c['dim']}  page     {site['url']}{c['reset']}")
    print(f"{c['dim']}  deciders TypeSafe Jev · {args.model} · recorded selectors "
          f"(no model){c['reset']}")
    if decide_only:
        print(f"{c['dim']}  mode     decide-only: nothing is typed or submitted{c['reset']}")
    print()

    runs = {"jev": [], "chat": [], "replay": []}
    try:
        for _ in range(args.repeats):
            jev = run_one("jev", site, args, decide_only)
            runs["jev"].append(report(jev, c))
            if decide_only:
                continue
            runs["chat"].append(report(run_one("chat", site, args, decide_only), c))
            if selectors := replay_args(jev.get("outcome", {})):
                replay = run_one("replay", site, args, decide_only, selectors)
                runs["replay"].append(report(replay, c))
    finally:
        if fixture:
            fixture.shutdown()

    summarise(runs, c)


def report(row, c):
    arm = row["arm"]
    if "error" in row:
        print(f"    {c['cyan']}{arm:<7}{c['reset']} {c['red']}✗{c['reset']} {row['error']}")
        return row
    secs = row["ms"] / 1000
    mark = f"{c['green']}✓{c['reset']}" if row["ok"] else f"{c['red']}✗{c['reset']}"
    if row["model_calls"]:
        decider = count(row["model_calls"], "chat call")
    elif calls := row["outcome"].get("calls"):
        decider = count(calls, "jev call")
    else:
        decider = "no model call"
    print(f"    {c['cyan']}{arm:<7}{c['reset']} {bar(secs)} {secs:6.2f}s  "
          f"{decider:<14} {count(row['tool_calls'], 'tool call'):<13} {mark}")
    return row


def summarise(runs, c):
    times = {arm: statistics.median(ms) for arm, rows in runs.items()
             if (ms := [r["ms"] for r in rows if r.get("ok")])}
    tokens = sum(r.get("tokens", 0) for r in runs["chat"])
    print(f"\n{c['dim']}  {'─' * 74}{c['reset']}")
    line = "  ".join(f"{c['bold']}{arm}{c['reset']} {ms / 1000:.2f}s" for arm, ms in times.items())
    print(f"  {c['bold']}median{c['reset']}   {line}")
    if "jev" in times and "chat" in times:
        print(f"           {c['yellow']}{times['chat'] / times['jev']:.0f}x{c['reset']} faster "
              f"than the chat model, on the same command and the same page")
    if "jev" in times and "replay" in times:
        print(f"           {c['yellow']}{times['jev'] / times['replay']:.0f}x{c['reset']} again "
              f"once the selectors are recorded: that run asks no one")
    if tokens:
        print(f"{c['dim']}  the chat arm spent {tokens} tokens reaching the same two "
              f"fields; jev's are not reported by the tool{c['reset']}")
    print()


if __name__ == "__main__":
    main()
