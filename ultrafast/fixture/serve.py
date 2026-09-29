"""Serves the vendored jev-ultrafast hotel fixture over loopback.

The fixture is a single self-contained SPA page, so every path returns it and
the client-side router does the rest. Same shape as
pandascript-vs-cdp/harness/login_fixture.py: stdlib only, no network, so the
deterministic task measures driver stacks rather than the internet.

Run: python fixture/serve.py [port]
"""

import http.server
import sys
import threading
from pathlib import Path

PAGE = (Path(__file__).with_name("index.html")).read_bytes()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *_args):
        pass


def serve(port=0):
    """Start the fixture in a daemon thread; returns (server, base_url)."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9290
    server, base = serve(port)
    print(base, flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
