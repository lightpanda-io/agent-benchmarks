"""Browser lifecycle for the pandascript-vs-cdp benchmark.

Launches lightpanda serve / headless Chrome, polls the CDP endpoint until
ready, and kills the whole process group afterwards. Launch-to-ready time is
returned so cold runs can be decomposed into launch + script in the report.
"""

import json
import os
import signal
import socket
import subprocess
import time
import urllib.request


class Browser:
    def __init__(self, proc, endpoint, ready_ms):
        self.proc = proc
        self.endpoint = endpoint
        self.ready_ms = ready_ms

    def kill(self):
        if self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGKILL)
        self.proc.wait()


def _wait_tcp(port, timeout_s=15.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.01)
    raise TimeoutError(f"port {port} not ready after {timeout_s}s")


def _wait_json_version(port, timeout_s=15.0):
    deadline = time.monotonic() + timeout_s
    url = f"http://127.0.0.1:{port}/json/version"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as resp:
                return json.load(resp)
        except OSError:
            time.sleep(0.01)
    raise TimeoutError(f"{url} not ready after {timeout_s}s")


def launch_lightpanda(lpd_path, port, extra_flags=()):
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        [lpd_path, "serve", "--host", "127.0.0.1", "--port", str(port), *extra_flags],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "LIGHTPANDA_DISABLE_TELEMETRY": "true"},
        start_new_session=True,
    )
    _wait_tcp(port)
    ready_ms = (time.perf_counter() - t0) * 1000
    return Browser(proc, f"ws://127.0.0.1:{port}", ready_ms)


CHROME_FLAGS = [
    "--headless=new",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-extensions",
]


def launch_chrome(chrome_path, port, user_data_dir):
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        [
            chrome_path,
            *CHROME_FLAGS,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _wait_json_version(port)
    ready_ms = (time.perf_counter() - t0) * 1000
    # http:// endpoint: puppeteer uses browserURL, playwright's connectOverCDP
    # resolves it via /json/version itself.
    return Browser(proc, f"http://127.0.0.1:{port}", ready_ms)


def prewarm_chrome_profile(chrome_path, port, user_data_dir):
    """First Chrome launch creates the profile (~hundreds of ms of one-time
    disk work). Do it once, untimed, so cold runs measure launch, not mkdir."""
    b = launch_chrome(chrome_path, port, user_data_dir)
    b.kill()
