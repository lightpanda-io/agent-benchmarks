# agent-browser `chat`, patched for benchmarking

`<suite>-ab-run` drives [agent-browser](https://github.com/vercel-labs/agent-browser)'s
own `chat` LLM loop. Stock agent-browser v0.38.2 can't run these suites
fairly, or at all against Gemini directly. `v0.38.2-gemini.patch` changes
three things in `cli/src/chat.rs`:

- **Budget.** Stock `chat` stops after 300 s or 50 steps. The patch reads
  `AGENT_BROWSER_CHAT_TIMEOUT_S` and `AGENT_BROWSER_CHAT_MAX_STEPS`, with the
  same defaults. The runners set the timeout from `--timeout` and the step
  cap to 1000, so time is the only limit, as for the Lightpanda agent.
- **Gemini thought signatures.** Google's OpenAI-compatible endpoint attaches
  a `thought_signature` to each tool call, in `extra_content`, and rejects the
  next request if it isn't sent back: `400 Function call is missing a
  thought_signature`. Stock `chat` drops it, so every multi-step task fails
  after its first tool call. The patch echoes `extra_content` back verbatim.
- **Retries.** A 429 or 5xx is retried within the deadline, after the
  provider's suggested delay (`Retry-After`, or Google's "retry in Ns"). Stock
  `chat` gives up on the first one, which under load turns a whole run into
  instant empty answers.

`cli/src/native/stream/chat.rs` (the dashboard stream) gets the same budget
change.

One more change is in `cli/src/native/actions.rs`:

- **Blocked URLs.** `AGENT_BROWSER_BLOCKED_URLS` (comma-separated, `*`
  wildcards, `network route` matching) becomes abort routes when the daemon
  starts. Fetch interception is switched on before each navigation and on
  every new tab, so blocked pages and in-page `fetch()` calls fail on both
  Chrome and Lightpanda. The runners set it with `--block-answer-sources`.
  Stock agent-browser only has an allow-list (`--allowed-domains`), which
  works per host and can't block just part of a site.

`upload` and `diff snapshot --baseline` read any local file, and Gemini used
`upload` to browse the local Hugging Face cache, where GAIA's
`metadata.parquet` holds every answer. So with `--block-answer-sources` the
runners start agent-browser under [bubblewrap](https://github.com/containers/bubblewrap)
(`bwrap`, which must be installed), with the Hugging Face cache and
`results/` mounted as empty tmpfs. The daemon and the browser it launches
inherit that view.

## Build

```bash
git clone https://github.com/vercel-labs/agent-browser competitors/agent-browser
cd competitors/agent-browser
git checkout v0.38.2
git apply ../agent-browser-chat/v0.38.2-gemini.patch
cd cli && cargo build --release
./target/release/agent-browser install      # downloads its Chrome
```

`competitors/agent-browser/` is gitignored. Pass the binary with
`--agent-browser competitors/agent-browser/cli/target/release/agent-browser`.
Copying it somewhere stable pins the version for a run.

## Run

```bash
export HF_TOKEN=hf_...          # GAIA is gated
export GOOGLE_API_KEY=...
export GEMINI_DIRECT=1          # Google's OpenAI-compatible endpoint, no gateway
AB=competitors/agent-browser/cli/target/release/agent-browser
LP=/path/to/lightpanda

# agent-browser + Chrome
uv run gaia-ab-run --agent-browser $AB --engine chrome \
  --model gemini-3.8-flash --workers 3 --timeout 1800

# agent-browser + Lightpanda as the engine
uv run gaia-ab-run --agent-browser $AB --engine lightpanda --lightpanda $LP \
  --model gemini-3.8-flash --workers 3 --timeout 1800

# Same flags for assistantbench-ab-run.
```

Each engine gets its own worker session names, so a Chrome daemon is never
reused for a Lightpanda run.

`--workers` is per run. With several runs at once, keep the total number of
agents within your Gemini quota: about 18 concurrent agents stayed under a
20M-input-tokens/min limit with `gemini-3.8-flash`.
