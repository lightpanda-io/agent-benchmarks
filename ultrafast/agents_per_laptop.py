# /// script
# requires-python = ">=3.10"
# dependencies = ["lightpanda>=0.4", "psutil>=6"]
# ///
"""How many browser agents fit on one laptop.

Launches N browsers on each engine against the same page, holds them all open at
once, and samples peak resident memory across every process they spawn.
Divide the machine's free memory by the per-agent figure and you get the number
of agents it holds.

There is no model and no API key here. An agent's memory is set by the browser
under it, not by whatever is choosing its actions, so the browser is all this
needs to measure.

    uv run agents_per_laptop.py                  # hostels in San Francisco
    uv run agents_per_laptop.py --url https://your.site --ladder 1,4,8
    uv run agents_per_laptop.py --local          # a bundled page, works offline

It loads a real booking site by default, because a toy fixture understates what
a real browser holds and flatters the result. Keep the ladder small on somebody
else's site: the default is two rungs, which is a handful of page loads.

The ratio moves with the weight of the page, so pick one that looks like your
workload. On an 8-core laptop with about 20 GB free, per agent:

    en.wikipedia.org article       62 MB   vs Chrome 1511 MB    24x
    travelodge.co.uk              113 MB   vs Chrome 1688 MB    15x
    hostelworld.com San Francisco 155 MB   vs Chrome 1655 MB    11x  <- the default
    agoda.com city page           226 MB   vs Chrome 1683 MB     7x
    airbnb.com search             369 MB   vs Chrome 1951 MB     5x

Chrome's figure barely moves across all five: it is paying for the engine. The
Lightpanda figure is almost entirely the page's own JavaScript.

Many sites answer a bot challenge instead of a page, and then the number compares
two challenge stubs rather than two browsers: booking.com and skyscanner.net do it
to both engines every time, and kayak.com does it intermittently. Measured against
booking.com this script reports a completely fictional 36x. A challenge can be
1.6 MB and carry an ordinary <title>, so neither size nor title tells you. The run
therefore reads the body text back and says so when the page it measured does not
look like a site, which is a warning to heed rather than a number to quote.
"""

import argparse
import contextlib
import http.server
import re
import shutil
import subprocess
import tempfile
import threading
import time

import psutil

DEFAULT_URL = "https://www.hostelworld.com/hostels/north-america/usa/san-francisco/"

WALL = re.compile(r"confirm that you are|are you a robot|access denied|unusual traffic|"
                  r"enable javascript and cookies|bot or not|verify you are human", re.I)

PAGE = b"""<!doctype html><html><head><meta charset="utf-8"><title>Fixture</title></head>
<body><h1>Stay finder</h1>
<form><label>Destination <input id="dest" name="dest"></label>
<label>Category <select id="cat"><option>All</option><option>Design</option></select></label>
<label><input type="checkbox" id="free"> Free cancellation</label>
<button type="submit">Search</button></form>
<ul id="results"><li>Casa Flora</li><li>The Glasshouse</li><li>Serra Lodge</li></ul>
<script>document.getElementById("dest").value = "Lisbon";</script>
</body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *_args):
        pass


def serve():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


def peak_rss_mb(roots, seconds=3.0, interval=0.1):
    """Highest total RSS over every root process and its descendants."""
    peak = 0
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        total = 0
        for root in roots:
            try:
                for proc in [root, *root.children(recursive=True)]:
                    with contextlib.suppress(psutil.Error):
                        total += proc.memory_info().rss
            except psutil.Error:
                pass
        peak = max(peak, total)
        time.sleep(interval)
    return peak / 1e6


def looks_like_a_page(page):
    """What the page actually says, so a bot challenge is not mistaken for a site.

    A challenge can be megabytes long and carry an ordinary <title>, so size and
    title prove nothing; only the body text does.
    """
    try:
        text = page.evaluate(script="document.body.innerText.slice(0, 2000)") or ""
    except Exception as err:
        return f"unreadable ({type(err).__name__})"

    if WALL.search(text):
        return "a bot challenge, not the site"
    if len(text.strip()) < 200:
        return "nearly empty"
    return None


def run_lightpanda(n, url):
    from lightpanda import Browser

    browsers, sessions = [], []
    try:
        for _ in range(n):
            browser = Browser()
            page = browser.new_session()
            page.goto(url=url)
            browsers.append(browser)
            sessions.append(page)
        complaint = looks_like_a_page(sessions[0])
        if complaint:
            print(f"    warning: this page reads as {complaint}; the number below is not"
                  f" a comparison of two sites")
        roots = [psutil.Process()]
        return peak_rss_mb(roots) - baseline_mb()
    finally:
        for page in sessions:
            with contextlib.suppress(Exception):
                page.close()
        for browser in browsers:
            with contextlib.suppress(Exception):
                browser.close()


def run_chrome(n, url, binary):
    flags = ["--headless=new", "--no-first-run", "--no-default-browser-check",
             "--disable-gpu", "--disable-background-networking"]
    procs, profiles = [], []
    try:
        for _ in range(n):
            profile = tempfile.mkdtemp(prefix="apl-chrome-")
            profiles.append(profile)
            procs.append(subprocess.Popen(
                [binary, *flags, f"--user-data-dir={profile}", url],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        time.sleep(2.5)
        roots = []
        for proc in procs:
            with contextlib.suppress(psutil.Error):
                roots.append(psutil.Process(proc.pid))
        return peak_rss_mb(roots)
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        for profile in profiles:
            shutil.rmtree(profile, ignore_errors=True)


_baseline = None


def baseline_mb():
    """This interpreter's own footprint, so the Lightpanda figure counts the
    sidecars rather than Python."""
    global _baseline
    if _baseline is None:
        _baseline = psutil.Process().memory_info().rss / 1e6
    return _baseline


def find_chrome(explicit):
    if explicit:
        return explicit
    for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def main():
    parser = argparse.ArgumentParser(description="How many browser agents fit on one laptop.")
    parser.add_argument("--ladder", default="1,4", help="agent counts to try")
    parser.add_argument("--chrome", default=None, help="path to a Chrome or Chromium binary")
    parser.add_argument("--url", default=DEFAULT_URL, help="page to load")
    parser.add_argument("--local", action="store_true",
                        help="use the bundled page instead of the network")
    args = parser.parse_args()

    baseline_mb()
    chrome = find_chrome(args.chrome)
    server = None
    url = args.url
    if args.local:
        server, url = serve()
    free_mb = psutil.virtual_memory().available / 1e6
    print(f"\n  {psutil.cpu_count()} cores, {free_mb / 1000:.1f} GB free, page {url}\n")
    print(f"  {'agents':>7}  {'Lightpanda':>22}  {'Chrome':>22}")

    per = {"lightpanda": [], "chrome": []}
    try:
        for n in [int(x) for x in args.ladder.split(",")]:
            lp = run_lightpanda(n, url)
            per["lightpanda"].append(lp / n)
            cells = [f"{lp:8.0f} MB  ({lp / n:5.0f}/agent)"]
            if chrome:
                ch = run_chrome(n, url, chrome)
                per["chrome"].append(ch / n)
                cells.append(f"{ch:8.0f} MB  ({ch / n:5.0f}/agent)")
            else:
                cells.append(" ".rjust(22))
            print(f"  {n:>7}  {cells[0]:>22}  {cells[1]:>22}")
    finally:
        if server:
            server.shutdown()

    def median(values):
        values = sorted(values)
        return values[len(values) // 2] if values else 0

    lp_each = median(per["lightpanda"])
    print(f"\n  Lightpanda: {lp_each:.0f} MB per agent, so this machine holds about"
          f" {free_mb / lp_each:.0f}")
    if per["chrome"]:
        ch_each = median(per["chrome"])
        print(f"  Chrome:     {ch_each:.0f} MB per agent, so this machine holds about"
              f" {free_mb / ch_each:.0f}")
        print(f"\n  {ch_each / lp_each:.0f}x less memory per agent.\n")
    else:
        print("  No Chrome or Chromium found; pass one with --chrome to compare.\n")


if __name__ == "__main__":
    main()
