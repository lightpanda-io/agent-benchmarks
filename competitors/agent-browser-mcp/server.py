"""Stdio MCP server wrapping the Vercel agent-browser CLI.

Each MCP tool execs `agent-browser --json …` for a single subcommand and
forwards the JSON. State (the open Chrome page) lives in agent-browser's
own background daemon, keyed by --session, so consecutive tool calls within
one MCP session reuse the same browser.

Run via stdio (not invoked by humans directly):

    python server.py

Env:
    AGENT_BROWSER_BIN     — agent-browser binary path (default: PATH lookup)
    AGENT_BROWSER_SESSION — session name to isolate parallel workers
                            (default: "mcp"). Each parallel benchmark
                            worker MUST pass a distinct value so they
                            don't share a Chrome.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

AGENT_BROWSER_BIN = os.environ.get("AGENT_BROWSER_BIN") or shutil.which("agent-browser") or "agent-browser"
SESSION = os.environ.get("AGENT_BROWSER_SESSION", "mcp")

# Per-call subprocess timeout. agent-browser's CLI has its own 25 s default
# action timeout (AGENT_BROWSER_DEFAULT_TIMEOUT), so this is a hard outer
# bound — should never fire unless the daemon is stuck.
CALL_TIMEOUT_S = 60.0


async def _exec(args: list[str]) -> str:
    """Run `agent-browser --session <SESSION> --json <args>` and return stdout.

    On non-zero exit or non-success JSON, return a JSON envelope mirroring
    agent-browser's own error shape so the model sees a uniform response."""
    cmd = [AGENT_BROWSER_BIN, "--session", SESSION, "--json", *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=CALL_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return json.dumps(
                {"success": False, "error": f"agent-browser call timed out after {CALL_TIMEOUT_S}s"}
            )
    except OSError as e:
        return json.dumps({"success": False, "error": f"failed to spawn agent-browser: {e!r}"})

    out = stdout.decode("utf-8", errors="replace").strip()
    err = stderr.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0 and not out:
        return json.dumps({"success": False, "error": err or f"exit {proc.returncode}"})
    return out or json.dumps({"success": True, "data": None})


# Tool definitions. Each entry is (name, description, inputSchema, builder).
# The builder takes the arguments dict and returns the agent-browser argv
# list (everything after `agent-browser --session X --json`).


def _str(t: str = "string") -> dict[str, Any]:
    return {"type": t}


def _build_open(args: dict[str, Any]) -> list[str]:
    return ["open", args["url"]]


def _build_snapshot(args: dict[str, Any]) -> list[str]:
    cmd = ["snapshot"]
    if args.get("interactive"):
        cmd.append("-i")
    if args.get("urls"):
        cmd.append("--urls")
    if args.get("compact"):
        cmd.append("-c")
    if (depth := args.get("depth")) is not None:
        cmd += ["-d", str(int(depth))]
    if selector := args.get("selector"):
        cmd += ["-s", selector]
    return cmd


def _build_click(args: dict[str, Any]) -> list[str]:
    return ["click", args["target"]]


def _build_fill(args: dict[str, Any]) -> list[str]:
    return ["fill", args["target"], args["text"]]


def _build_press(args: dict[str, Any]) -> list[str]:
    return ["press", args["key"]]


def _build_find(args: dict[str, Any]) -> list[str]:
    cmd = ["find", args["by"], args["value"], args["action"]]
    if v := args.get("value_after"):
        cmd.append(v)
    if name := args.get("name"):
        cmd += ["--name", name]
    if args.get("exact"):
        cmd.append("--exact")
    return cmd


def _build_wait(args: dict[str, Any]) -> list[str]:
    cmd = ["wait", args["target"]]
    if state := args.get("state"):
        cmd += ["--state", state]
    return cmd


def _build_get(args: dict[str, Any]) -> list[str]:
    what = args["what"]
    cmd = ["get", what]
    if what in ("text", "html", "value", "attr", "count", "box", "styles"):
        cmd.append(args["target"])
    if what == "attr":
        cmd.append(args["attr"])
    return cmd


def _build_eval(args: dict[str, Any]) -> list[str]:
    return ["eval", args["js"]]


def _build_back(_args: dict[str, Any]) -> list[str]:
    return ["back"]


def _build_forward(_args: dict[str, Any]) -> list[str]:
    return ["forward"]


def _build_reload(_args: dict[str, Any]) -> list[str]:
    return ["reload"]


def _build_close(_args: dict[str, Any]) -> list[str]:
    return ["close"]


TOOL_TABLE: list[tuple[str, str, dict[str, Any], Any]] = [
    (
        "open",
        "Navigate the browser to a URL. Aliases: goto, navigate. Launches a Chrome page in the agent-browser daemon if one isn't already open for this session.",
        {
            "type": "object",
            "properties": {"url": _str()},
            "required": ["url"],
        },
        _build_open,
    ),
    (
        "snapshot",
        "Get the accessibility tree of the current page with @eN refs you can pass to click/fill/get. Use interactive=true for inputs/buttons/links only (the most useful filter for an agent); urls=true to include link hrefs.",
        {
            "type": "object",
            "properties": {
                "interactive": {"type": "boolean"},
                "urls": {"type": "boolean"},
                "compact": {"type": "boolean"},
                "depth": {"type": "integer"},
                "selector": _str(),
            },
        },
        _build_snapshot,
    ),
    (
        "click",
        "Click an element. Target is either an @eN ref from a snapshot, or a CSS selector.",
        {
            "type": "object",
            "properties": {"target": _str()},
            "required": ["target"],
        },
        _build_click,
    ),
    (
        "fill",
        "Clear and fill text into an input. Target is @eN or a CSS selector.",
        {
            "type": "object",
            "properties": {"target": _str(), "text": _str()},
            "required": ["target", "text"],
        },
        _build_fill,
    ),
    (
        "press",
        "Press a keyboard key (e.g. Enter, Tab, Control+a).",
        {
            "type": "object",
            "properties": {"key": _str()},
            "required": ["key"],
        },
        _build_press,
    ),
    (
        "find",
        "Find an element by semantic locator and perform an action on it. `by` ∈ {role,text,label,placeholder,alt,title,testid,first,last,nth}. `action` ∈ {click,fill,type,hover,focus,check,uncheck,text}. For role+name, set name. For text matches, set exact=true to require equality.",
        {
            "type": "object",
            "properties": {
                "by": {
                    "type": "string",
                    "enum": [
                        "role",
                        "text",
                        "label",
                        "placeholder",
                        "alt",
                        "title",
                        "testid",
                        "first",
                        "last",
                    ],
                },
                "value": _str(),
                "action": {
                    "type": "string",
                    "enum": [
                        "click",
                        "fill",
                        "type",
                        "hover",
                        "focus",
                        "check",
                        "uncheck",
                        "text",
                    ],
                },
                "value_after": {
                    "type": "string",
                    "description": "Text to fill/type, for fill/type actions.",
                },
                "name": _str(),
                "exact": {"type": "boolean"},
            },
            "required": ["by", "value", "action"],
        },
        _build_find,
    ),
    (
        "wait",
        "Wait for a condition. target is a CSS selector OR an integer-as-string (ms). state ∈ {visible,hidden,attached,detached} for selectors.",
        {
            "type": "object",
            "properties": {"target": _str(), "state": _str()},
            "required": ["target"],
        },
        _build_wait,
    ),
    (
        "get",
        "Read information from the page. what ∈ {text,html,value,attr,title,url,count,box,styles}. For text/html/value/count/box/styles, pass target=@eN-or-selector. For attr, also pass attr=name.",
        {
            "type": "object",
            "properties": {
                "what": {
                    "type": "string",
                    "enum": [
                        "text",
                        "html",
                        "value",
                        "attr",
                        "title",
                        "url",
                        "count",
                        "box",
                        "styles",
                    ],
                },
                "target": _str(),
                "attr": _str(),
            },
            "required": ["what"],
        },
        _build_get,
    ),
    (
        "eval",
        "Run a JavaScript expression in the page and return the result.",
        {
            "type": "object",
            "properties": {"js": _str()},
            "required": ["js"],
        },
        _build_eval,
    ),
    ("back", "Go back in history.", {"type": "object", "properties": {}}, _build_back),
    (
        "forward",
        "Go forward in history.",
        {"type": "object", "properties": {}},
        _build_forward,
    ),
    (
        "reload",
        "Reload the current page.",
        {"type": "object", "properties": {}},
        _build_reload,
    ),
    (
        "close",
        "Close the browser session. Optional — the daemon cleans up on idle.",
        {"type": "object", "properties": {}},
        _build_close,
    ),
]

_BUILDERS = {name: builder for name, _, _, builder in TOOL_TABLE}


def _make_server() -> Server:
    server: Server = Server("agent-browser")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(name=name, description=desc, inputSchema=schema)
            for name, desc, schema, _ in TOOL_TABLE
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        builder = _BUILDERS.get(name)
        if builder is None:
            return [
                TextContent(
                    type="text",
                    text=json.dumps({"success": False, "error": f"unknown tool: {name}"}),
                )
            ]
        try:
            argv = builder(arguments or {})
        except KeyError as e:
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {"success": False, "error": f"missing required argument: {e.args[0]}"}
                    ),
                )
            ]
        result = await _exec(argv)
        return [TextContent(type="text", text=result)]

    return server


async def main() -> None:
    server = _make_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
