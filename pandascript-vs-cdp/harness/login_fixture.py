"""Local login fixture mimicking the HN login flow's markup and shape:

  GET  /login      -> form with input[name=acct] / input[name=pw]
  POST /login      -> sets a session cookie, redirects to /
  GET  /           -> front page; #logout link present iff logged in
  GET  /user?id=X  -> profile page; karma cell at the same selector HN uses

Used when live news.ycombinator.com login is captcha-blocked; also gives a
zero-network-noise measurement of pure driver-stack overhead.

Run: python harness/login_fixture.py [port]
"""

import http.server
import sys
import urllib.parse

USER = "bench_user"
PASS = "bench_pass"
COOKIE = "fixture_session=ok"

LOGIN_PAGE = b"""<html><head><title>Login</title></head><body>
<b>Login</b>
<form action="/login" method="post">
<table border="0">
<tr><td>username:</td><td><input type="text" name="acct" size="20" autofocus="t"></td></tr>
<tr><td>password:</td><td><input type="password" name="pw" size="20"></td></tr>
</table>
<input type="submit" value="login">
</form>
</body></html>"""

FRONT_LOGGED_IN = b"""<html><head><title>Fixture News</title></head><body>
<table id="hnmain"><tr><td>
<span class="pagetop"><a href="/user?id=bench_user">bench_user</a> (250) |
<a id="logout" href="/logout">logout</a></span>
</td></tr></table>
</body></html>"""

BAD_LOGIN = b"""<html><body>Bad login.</body></html>"""


def user_page(name):
    # #hnmain > table > table nesting matches news.ycombinator.com's user page,
    # so the benchmark scripts' selector works unchanged against the fixture.
    return f"""<html><head><title>Profile: {name}</title></head><body>
<table id="hnmain"><tr><td>
<table border="0"><tr><td>
<table border="0">
<tr><td>user:</td><td>{name}</td></tr>
<tr><td>created:</td><td>365 days ago</td></tr>
<tr><td>karma:</td><td>250</td></tr>
<tr><td>about:</td><td></td></tr>
</table>
</td></tr></table>
</td></tr></table>
</body></html>""".encode()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, body, status=200, headers=()):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/login":
            self._send(LOGIN_PAGE)
        elif url.path == "/":
            if COOKIE in (self.headers.get("Cookie") or ""):
                self._send(FRONT_LOGGED_IN)
            else:
                self._send(LOGIN_PAGE)
        elif url.path == "/user":
            name = urllib.parse.parse_qs(url.query).get("id", ["?"])[0]
            self._send(user_page(name))
        else:
            self._send(b"not found", status=404)

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/login":
            return self._send(b"not found", status=404)
        length = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        if form.get("acct", [""])[0] == USER and form.get("pw", [""])[0] == PASS:
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", COOKIE + "; Path=/")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._send(BAD_LOGIN)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9280
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"login fixture on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()
