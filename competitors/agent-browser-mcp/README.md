# agent-browser-mcp

Thin stdio MCP server that exposes [Vercel agent-browser](https://github.com/vercel-labs/agent-browser)'s CLI as a Model Context Protocol tool surface.

Why this exists
---------------
agent-browser ships its own LLM-driven `chat` loop, but doesn't expose its tools as an MCP server. To benchmark agent-browser side-by-side with Lightpanda — where the *only* variable is the browser engine + tool surface, and the LLM brain is held constant — both browsers need to be drivable by the same agent harness. Lightpanda has `lightpanda mcp` built-in; this wrapper provides the equivalent for agent-browser.

It's intentionally minimal — one MCP tool per relevant agent-browser subcommand, each tool just shells out to `agent-browser --json …` and forwards the JSON. No abstraction, no schema reinvention, no caching.

Running
-------
The server is invoked by an MCP client (e.g. Claude Code's `--mcp-config`). It is not run by humans directly. A typical client config:

```json
{
  "mcpServers": {
    "agent-browser": {
      "command": "/path/to/benchmarks/.venv/bin/python",
      "args": ["/path/to/benchmarks/competitors/agent-browser-mcp/server.py"],
      "env": {
        "AGENT_BROWSER_BIN": "/home/you/.npm-global/bin/agent-browser",
        "AGENT_BROWSER_SESSION": "bench-w0"
      }
    }
  }
}
```

`AGENT_BROWSER_SESSION` isolates parallel workers — each worker should get its own session name so they don't share a Chrome.

Tools exposed
-------------
- `open(url)` — navigate
- `snapshot(interactive?, urls?, compact?, depth?, selector?)` — accessibility tree with `@eN` refs
- `click(target)` — click by `@eN` ref or CSS selector
- `fill(target, text)`
- `press(key)`
- `find(by, value, action, name?, exact?)` — semantic locator (role/text/label/placeholder/alt/title/testid)
- `wait(selector_or_ms, state?)` — wait for element / time / text / load state
- `get(what, target?)` — text / html / value / url / title / attr
- `back()`, `forward()`, `reload()`
- `eval(js)` — run JS
- `close()`

This is a benchmarking surface — not the full agent-browser command set. Add tools here if the harness needs more; the pattern is repetitive.

Deps
----
Uses the `mcp` Python SDK. Add via `uv add mcp` in the parent `benchmarks/` project.
