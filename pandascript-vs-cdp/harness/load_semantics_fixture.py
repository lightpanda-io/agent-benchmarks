"""Fixture server for the load-event-semantics experiment.

Serves pages that include scripts of controlled type and latency so the
time-to-load-event can be compared across engines:

  /page?kind=async&n=4&ms=300     4 parser-inserted async scripts, 300 ms each
  /page?kind=defer&n=4&ms=300     4 deferred scripts
  /page?kind=dynamic&n=4&ms=300   4 dynamically-injected scripts
  /page?kind=none                 no scripts (baseline)
  /slow.js?ms=300                 script body served after a 300 ms delay

Run: python harness/load_semantics_fixture.py [port]
"""

import http.server
import sys
import time
import urllib.parse


def page(kind, n, ms):
    if kind == "classic":
        scripts = "\n".join(
            f'<script src="/slow.js?ms={ms}&i={i}"></script>' for i in range(n))
    elif kind == "async":
        scripts = "\n".join(
            f'<script async src="/slow.js?ms={ms}&i={i}"></script>' for i in range(n))
    elif kind == "defer":
        scripts = "\n".join(
            f'<script defer src="/slow.js?ms={ms}&i={i}"></script>' for i in range(n))
    elif kind == "dynamic":
        tags = ";".join(
            f'var s{i}=document.createElement("script");s{i}.src="/slow.js?ms={ms}&i={i}";'
            f'document.head.appendChild(s{i})' for i in range(n))
        scripts = f"<script>{tags}</script>"
    else:
        scripts = ""
    return f"""<!DOCTYPE html>
<html><head><title>load-semantics {kind}</title>{scripts}</head>
<body><h1 id="marker">{kind}</h1></body></html>""".encode()


def site_page(n_assets, ms):
    scripts = "\n".join(
        f'<script src="/asset/{k}.js?ms={ms}"></script>' for k in range(n_assets))
    return f"""<!DOCTYPE html>
<html><head><title>site page</title>{scripts}</head>
<body><h1 id="marker">page</h1></body></html>""".encode()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        # Multi-page "site" with shared cacheable assets, for cache-path
        # experiments: /site/<page-number>?assets=3&ms=30 includes the same
        # /asset/K.js on every page, served with a long max-age.
        if url.path.startswith("/asset/"):
            time.sleep(int(q.get("ms", ["0"])[0]) / 1000)
            body = b"window.__asset = (window.__asset || 0) + 1;"
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Cache-Control", "public, max-age=3600")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if url.path.startswith("/site/"):
            body = site_page(int(q.get("assets", ["3"])[0]),
                             int(q.get("ms", ["30"])[0]))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if url.path == "/slow.js":
            time.sleep(int(q.get("ms", ["0"])[0]) / 1000)
            body = b"window.__loaded = (window.__loaded || 0) + 1;"
            ctype = "application/javascript"
        elif url.path == "/page":
            body = page(q.get("kind", ["none"])[0],
                        int(q.get("n", ["0"])[0]),
                        int(q.get("ms", ["0"])[0]))
            ctype = "text/html; charset=utf-8"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9300
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"load-semantics fixture on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()
