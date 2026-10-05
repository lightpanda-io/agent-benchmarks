"""Chrome adapter: headless Chrome over a raw CDP websocket.

The observer is upstream's own `snapshot.js`, vendored unmodified, so Chrome is
observed the way browser-use built it to be observed -- viewport clipping,
WeakMap node identity, hit-tested execution. That is deliberately not the same
observer the Lightpanda arm uses, because there is no layout to port it to.
What is identical across the two arms is everything after the table: the bound,
the state JSON, the questions, and both deciders.
"""

import json
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect

from . import table

SNAPSHOT = (Path(__file__).with_name("snapshot.js")).read_text()

FLAGS = [
    "--headless=new",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-extensions",
]
VIEWPORT = {"width": 1120, "height": 780}
SETTLE_MS = 50
COMBOBOX_SETTLE_MS = 200


def _free_port():
    """A campaign in another worktree may already own a fixed debugging port."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _chrome_path():
    for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("no Chrome binary on PATH")


class Chrome:
    name = "chrome"

    def __init__(self, url, binary=None, port=None):
        port = port or _free_port()
        self.calls = 0
        self.call_ms = 0.0
        self._profile = tempfile.mkdtemp(prefix="ultrafast-chrome-")
        self._proc = subprocess.Popen(
            [binary or _chrome_path(), *FLAGS, f"--remote-debugging-port={port}",
             f"--user-data-dir={self._profile}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        endpoint = self._wait_for_endpoint(port)
        self._ws = connect(endpoint, max_size=64 * 1024 * 1024, open_timeout=20)
        self._id = 0
        self._settle = 0
        target = self._send("Target.createTarget", url="about:blank")["targetId"]
        self._session = self._send("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
        self._call("Emulation.setDeviceMetricsOverride", **VIEWPORT, deviceScaleFactor=1, mobile=False)
        # Keep rAF and menus alive in a background tab, as upstream does.
        self._call("Emulation.setFocusEmulationEnabled", enabled=True)
        self._call("Page.navigate", url=url)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self._evaluate("document.readyState") == "complete":
                break
            time.sleep(0.02)

    @staticmethod
    def _wait_for_endpoint(port, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
                    return json.load(r)["webSocketDebuggerUrl"]
            except Exception:
                time.sleep(0.05)
        raise RuntimeError("Chrome did not expose a CDP endpoint")

    def _send(self, method, session=None, **params):
        self._id += 1
        message = {"id": self._id, "method": method, "params": params}
        if session:
            message["sessionId"] = session
        started = time.perf_counter()
        self._ws.send(json.dumps(message))
        while True:
            reply = json.loads(self._ws.recv())
            if reply.get("id") == self._id:
                break
        self.call_ms += (time.perf_counter() - started) * 1000
        self.calls += 1
        if "error" in reply:
            raise RuntimeError(f"{method}: {reply['error'].get('message')}")
        return reply.get("result", {})

    def _call(self, method, **params):
        return self._send(method, session=self._session, **params)

    def _evaluate(self, expression, await_promise=False):
        result = self._call("Runtime.evaluate", expression=expression,
                            returnByValue=True, awaitPromise=await_promise)
        if result.get("exceptionDetails"):
            raise table.Stale("Document changed during evaluation")
        return result.get("result", {}).get("value")

    def observe(self):
        if self._settle:
            time.sleep(self._settle / 1000)
            self._settle = 0
        for attempt in range(10):
            try:
                snapshot = self._evaluate(SNAPSHOT)
                break
            except table.Stale:
                if attempt == 9:
                    raise
                time.sleep(0.02)
        if snapshot is None:
            raise table.Stale("Document is navigating")
        elements, omitted = table.bound(self._candidates(snapshot["actions"]))
        scroll = snapshot["scroll"]
        return table.State(
            url=snapshot["url"],
            title=snapshot["title"],
            text=snapshot["text"],
            elements=elements,
            omitted=omitted + snapshot.get("omitted_actions", 0),
            can_scroll_down=scroll["y"] + snapshot["h"] < scroll["height"] - 2,
            can_scroll_up=scroll["y"] > 0,
        )

    @staticmethod
    def _candidates(actions):
        """snapshot.js emits one entry per (node, operation); fold them back
        into one element per node, the way upstream's action_space does."""
        kinds = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
        by_node, order = {}, []
        for action in actions:
            op = kinds.get(action["kind"])
            if op is None:
                continue
            node = action["node"]
            if node not in by_node:
                checked = action.get("checked")
                by_node[node] = table.Element(
                    node=node,
                    role=action["role"],
                    label=action["label"].split(" → ")[0],
                    value=action.get("current_value", action.get("value")) or None,
                    checked=None if checked is None else checked == "true",
                )
                order.append(node)
            element = by_node[node]
            if op not in element.ops:
                element.ops.append(op)
            if op == "SELECT":
                element.options.append((action["value"], action["label"].partition(" → ")[2]))
        return [by_node[node] for node in order]

    def act(self, operation, element, value=None, text=None):
        if operation == "WAIT":
            time.sleep(0.1)
            return
        if operation in ("SCROLL_DOWN", "SCROLL_UP"):
            delta = 560 if operation == "SCROLL_DOWN" else -560
            self._call("Input.dispatchMouseEvent", type="mouseWheel", x=550, y=650, deltaX=0, deltaY=delta)
            self._settle = SETTLE_MS
            return
        # Code-owned node ids: geometry and occlusion are resolved again here,
        # immediately before input, never taken from the observation.
        request = {"node": element.node, "kind": operation, "value": value}
        target = self._evaluate("""(action => {
          const e=window.__jevFast?.nodes.get(action.node);
          if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
              !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
          if (action.kind==='TYPE_TEXT' && (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
          const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
          if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight) return null;
          if (!e.contains(document.elementFromPoint(x,y))) return null;
          if (action.kind==='SELECT') {
            if (e.tagName!=='SELECT' || ![...e.options].some(o=>o.value===action.value &&
                !o.disabled && !o.closest('optgroup[disabled]'))) return null;
            e.value=action.value;
            e.dispatchEvent(new Event('input',{bubbles:true}));
            e.dispatchEvent(new Event('change',{bubbles:true}));
          }
          return {x,y};
        })(""" + json.dumps(request) + ")")
        if target is None:
            raise table.Stale("Target changed or is covered. Observe again.")
        if operation != "SELECT":
            for event in ("mousePressed", "mouseReleased"):
                self._call("Input.dispatchMouseEvent", type=event, x=target["x"], y=target["y"],
                           button="left", clickCount=1)
            if operation == "TYPE_TEXT":
                self._call("Input.dispatchKeyEvent", type="keyDown", key="a", code="KeyA",
                           modifiers=2, commands=["selectAll"])
                self._call("Input.dispatchKeyEvent", type="keyUp", key="a", code="KeyA", modifiers=2)
                self._call("Input.insertText", text=text)
        self._settle = COMBOBOX_SETTLE_MS if element.role == "combobox" else SETTLE_MS

    def document(self):
        """The whole rendered document, for the independent checker only. Not
        the observation the decider saw, and not counted: it runs after the
        clock stops, and it must not be viewport-clipped or the two arms would
        be checked against different evidence."""
        url, title, text = self._evaluate(
            "[location.href, document.title, document.body.innerText]")
        self.calls -= 1
        return table.State(url=url, title=title, text=text, elements=[])

    def close(self):
        try:
            self._ws.close()
        finally:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            shutil.rmtree(self._profile, ignore_errors=True)
