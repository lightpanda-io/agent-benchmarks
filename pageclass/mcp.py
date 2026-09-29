"""Minimal MCP stdio client for driving one `lightpanda mcp` process."""

import json
import os
import subprocess


class Mcp:
    """One lightpanda MCP server over stdio. Use as a context manager."""

    def __init__(self, binary: str, env: dict | None = None):
        self.binary = binary
        self.env = env
        self._proc: subprocess.Popen | None = None
        self._id = 0

    def __enter__(self):
        self._proc = subprocess.Popen(
            [self.binary, "mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env={**os.environ, **(self.env or {})},
        )
        self.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pageclass-corpus", "version": "0"},
        })
        self._notify("notifications/initialized")
        return self

    def __exit__(self, *exc):
        if self._proc is None:
            return
        try:
            self._proc.stdin.close()
            self._proc.wait(timeout=10)
        except Exception:
            self._proc.kill()

    def _notify(self, method: str, params: dict | None = None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()

    def request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        wanted = self._id
        msg = {"jsonrpc": "2.0", "id": wanted, "method": method}
        if params is not None:
            msg["params"] = params
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()
        while True:
            line = self._proc.stdout.readline()
            if not line:
                raise RuntimeError("mcp server closed the pipe")
            try:
                reply = json.loads(line)
            except json.JSONDecodeError:
                continue  # a log line, not a response
            if reply.get("id") == wanted:
                return reply

    def tool(self, name: str, arguments: dict):
        """Call a tool. Returns the parsed JSON result, or the raw text when the
        tool answered with something that is not JSON, which is how it reports
        a failure in band."""
        reply = self.request("tools/call", {"name": name, "arguments": arguments})
        if "error" in reply:
            return f"rpc error: {reply['error'].get('message', reply['error'])}"
        content = reply.get("result", {}).get("content", [{}])
        text = content[0].get("text", "") if content else ""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def page_class(self, url: str, judge: bool, timeout_ms: int = 20000):
        return self.tool("pageClass", {"url": url, "judge": judge, "timeout": timeout_ms})
