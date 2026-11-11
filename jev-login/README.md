# jev-login

Times one command, `/login`, decided three ways on the same page by the same
binary: TypeSafe's Jev, a chat model, and the selectors a Jev run
recorded. The branch under test makes `/login` fall back to the chat prompt
when `TYPESAFE_API_KEY` is unset, so switching arms means unsetting one
environment variable.

```console
uv run python jev-login/demo.py              # HN-shaped local fixture
uv run python jev-login/demo.py --repeats 5
uv run python jev-login/demo.py --site hn    # live HN, decide-only
```

| flag | default | |
| --- | --- | --- |
| `--site` | `fixture` | `hn` uses the real login page |
| `--live` | off | on `--site hn`, really sign in instead of deciding only |
| `--repeats` | `1` | runs per arm |
| `--provider` / `--model` | `anthropic` / `claude-haiku-5-5` | the chat arm |
| `--base-url` | none | an OpenAI-compatible endpoint, with `--provider openai` |
| `--timeout` | `300` | seconds per run |
| `--jev-label` | `jev` | the Jev arm's name, for a [local decider](#local-decider) |

The Jev arm needs `TYPESAFE_API_KEY` and the chat arm needs a key for its
provider. The fixture supplies its own throwaway credentials. `--site hn`
reads `$LP_HN_USERNAME` and `$LP_HN_PASSWORD` and passes them as `$LP_*`
placeholders, which resolve inside the browser, so the secrets stay out of the
model's context and the result text.

Any OpenAI-compatible endpoint works as the chat arm. For Groq:

```console
OPENAI_API_KEY=$GROQ_API_KEY uv run python jev-login/demo.py --provider openai \
  --base-url https://api.groq.com/openai/v1 --model openai/gpt-oss-120b
```

Groq's free tier allows 8k tokens per minute, less than the chat arm's first
request. On the paid tier the limit is 250k, which back-to-back repeats still
exhaust; run them one at a time with a pause between.

## Measured

Runs on 2026-10-28, on one laptop, with lightpanda `3108ce691` (the
`jev-fallback` branch). Jev and the replay ran alongside every chat model run,
so they have 35 runs each. Medians and ranges are over the runs that signed
in.

| arm | signed in | median | range | round trips | per round trip |
| --- | --- | --- | --- | --- | --- |
| jev | 35/35 | 0.36s | 0.32–0.58s | 2 Jev calls | |
| claude-haiku-5-5 | 5/5 | 6.3s | 5.9–6.9s | 5 | 1.3s |
| gpt-oss-120b (Groq) | 8/10 | 7.1s | 5.3–9.3s | 9–16 | 0.7s |
| gemini-3.5-flash-lite | 5/5 | 10.4s | 8.8–15.5s | 7–12 | 1.1s |
| gemini-3.8-flash | 5/5 | 21.7s | 18.7–27.8s | 7–11 | 2.7s |
| gpt-oss-20b (Groq) | 0/10 | | | 2–6 | |
| replay | 35/35 | 0.005s | up to 0.02s | none | |

Jev is 17x faster than Haiku, the fastest chat model, on the median. Haiku
batches its tool calls (all three `nodeDetails` in one turn, both `fill`s in
the next) and always finishes in five. gpt-oss-120b on Groq has the quickest
round trips but makes two to three times as many, so it lands just behind
Haiku. Its two failures were its own: one run filled the username and
submitted without the password, the other filled both and stopped. gpt-oss-20b
never signed in. It kept calling `tree` with a `url` it made up
(`about:blank`, `https://example.com/login`), which navigated away from the
login page.

Effort stays at the REPL default, `low`. That is the floor for
gemini-3.8-flash: it answers `minimal` with HTTP 400, and `none` sends no
thinking level, which leaves the model on its own default.

On the live HN page, Jev picks from 7 candidate fields and controls in 0.39s.
Its `submitSelector` comes back as
`form:has(input[type="submit"][value="login"]) > input`. HN serves a sign-in
form and a create-account form with identically named inputs, and telling them
apart is the hard part of this page.

## Local decider

[StartLux-Decision](https://huggingface.co/startlux-models) ships GGUF builds
plus a small Python server (`startlux_decision.gguf_server`) that answers
System One requests from llama-server's next-token log-probabilities. It
accepts the same `state`/`questions` and returns the same answer shapes as Jev,
and `TYPESAFE_BASE_URL` points the browser at it, so no browser change is
needed. The weights are CC BY-NC 4.0.

On the GPU machine (llama.cpp ≥ b10454):

```console
hf download startlux-models/StartLux-Decision-v1-2B-Q8_0-GGUF --local-dir 2b
uv venv && uv pip install transformers torch   # tokenizer only, no torch model is loaded
llama-server -m 2b/StartLux-Decision-2B-Q8_0.gguf -ngl 99 -c 20480 --parallel 1 --ctx-checkpoints 0 -b 16384 -ub 16384 --port 8081
cd 2b && python -m startlux_decision.gguf_server --model-dir . --llama http://127.0.0.1:8081 --port 8090
```

The decision server binds 127.0.0.1, so tunnel to it, then run the demo with
any non-empty key:

```console
ssh -N -L 8090:127.0.0.1:8090 gpu-host
TYPESAFE_BASE_URL=http://127.0.0.1:8090 TYPESAFE_API_KEY=local \
  uv run python jev-login/demo.py --jev-label startlux-2b --repeats 5
```

Measured 2026-11-11 on a Quadro RTX 5000 (16 GB, Turing), reached over Wi-Fi
through an SSH tunnel; 5 fixture runs per model, hosted Jev rerun the same day:

| decider | signed in | median | range | server compute |
| --- | --- | --- | --- | --- |
| jev (hosted) | 5/5 | 0.51s | 0.49–0.57s | |
| startlux-2b Q8_0 | 5/5 | 0.58s | 0.49–0.71s | ~0.40s |
| startlux-v1.1-2b-instant Q8_0 | 5/5 | 0.66s | 0.48–0.77s | ~0.40s |
| startlux-4b Q4_K_M | 5/5 | 1.35s | 1.10–1.43s | ~0.87s |
| startlux-9b Q8_0 | 5/5 | 2.40s | 1.60–3.63s | ~1.35s |

Both models make the same 2 calls as Jev: four questions in parallel, then the
check. On live HN (decide-only) the 2B and the 4B return the same
`submitSelector` as Jev, in 0.82s and 1.19s. The v1.1 2B Instant does not:
in 7 of 7 runs it takes the username field from the create-account form
(`form:has(input[name="creating"]...) input[name="acct"]`) and the password
and submit from the sign-in form, so a live login would fail. Decide-only
marks ✓ whenever both selectors come back, so check the selectors themselves.
The Instant model needs the `startlux-decide` package
([StartLuxLabs/StartLux-Decision](https://github.com/StartLuxLabs/StartLux-Decision),
plus `jinja2`, which it does not declare) and one llama-server slot
(`--parallel 1 --ctx-checkpoints 0 -b 16384 -ub 16384`,
`STARTLUX_LLAMACPP_BIDIR=server`), since it reads prompts bidirectionally.

The server time is GPU compute, not llama-server tuning. Timed on the GPU host
with a 4-question, 2,532-token request (20 runs each), the v1 2B took 531 ms
with one slot and a 16k batch, 595 ms with four slots at the default batch,
and 597–609 ms with four slots and a 4k batch or with flash attention: about
4,500 tokens/s in every case. The 4B and 9B are slower in proportion to their
size, so only a faster GPU closes the gap to hosted Jev. The rest of the
spread is the Wi-Fi link (the 9B's is mostly that).

## Reading the output

`time` runs from the moment `/login` starts to the last thing the run does,
taken from `--trace`. The opening `goto` is setup that both arms pay equally,
so it is excluded.

`decider` counts round trips, and the arms differ most here. Jev makes one
structured call to pick the fields and a second to check the page agreed. The
chat arm needs a turn for each round of tool calls: it reads the tree,
inspects each candidate with `nodeDetails`, fills each field, clicks and
re-reads. The fallback prompt hands it the credential placeholders from the
command and tells it `login` and `acceptCookies` need the TypeSafe key, so it
doesn't spend turns on `getEnv` or on tools that will fail.

`tool calls` counts browser work inside the command. The Jev and replay arms
show 1 because `login` is itself the tool.

`✓` means the run ended on the same site as the login page, at another URL,
under another title. Leaving the login page is not enough: gpt-oss-20b
navigates to URLs it invents. The Jev and replay arms also report
`"loggedIn": true` from their own verification re-read.

## Screencast

`screencast.sh` opens two REPLs side by side in tmux on the fixture, Jev on
the left and Claude Haiku 5.5 on the right, and mirrors keystrokes to both
panes, so one typed `/login` starts both at once. `screencast.tape` drives it
under [VHS](https://github.com/charmbracelet/vhs) and records 1080p video:

```console
vhs screencast.tape && ./screencast.sh stop && uv run python screencast_timers.py
```

Each pane writes a `--trace`. `screencast_timers.py` reads both and burns a
clock into each pane that stops at that pane's traced time, then prints every
anchor it aligned the video on (Enter, Jev's result, each chat tool call,
the end of the chat turn) beside its traced time. The output is
`results/jev-login-race-timed.mp4`.

Each take is a fresh live run, so the chat side's time changes from take to
take. The clocks always show that take's own traced numbers. A long chat
summary can reach the clock area at the bottom of the right pane; check the
end of each take.

`screencast_local.sh` and `screencast_local.tape` record the same race with a
[local decider](#local-decider) on the left, on the machine that runs it.
Below the two REPLs, a strip shows llama-server's log trimmed to one line per
forward pass, and btop's GPU panel, so the decision is seen happening on the
local GPU. Run it there with the decision server up and `ANTHROPIC_API_KEY`
set:

```console
vhs screencast_local.tape && ./screencast_local.sh stop && uv run python screencast_timers.py --layout local
```

The output is `results/jev-login-local-race-timed.mp4`. With less height per
REPL, the clocks sit beside each pane's banner instead of below the output.
On the P53 take, StartLux-2B signed in after 0.48s and Haiku after 5.06s.

`screencast_endcard.py` appends a 5-second card charting every decider's
median from the [local decider](#local-decider) table, so the race is followed
by the other sizes and by hosted Jev:

```console
uv run --with pillow python screencast_endcard.py
```

The numbers are typed into the script from that table, not measured by it.
The output is `results/jev-login-local-race-card.mp4`.

## Caveats

The default page is a local fixture of HN's login markup (`input[name=acct]`
and `input[name=pw]` in a borderless table). HN rate-limits repeated logins
per IP, and a benchmark that signs in fifteen times per arm trips validation.
`--site hn` checks the real page by asking Jev which fields it would use, and
types nothing, so it is safe to repeat. `--site hn --live` signs in for real,
three times per repeat (once per arm). Use it to check the real page end to
end, and don't loop it to collect a median.

The chat arm's time goes on round trips. Haiku's five turns take about 1.3
seconds each and write about 600 output tokens in total. Faster inference
shortens each turn without removing any: Groq's gpt-oss-120b turns take 0.7s,
and with three times as many of them it still lands behind Haiku. Jev makes
two calls.

Haiku reads almost all of its input from Anthropic's prompt cache (74k of 75k
tokens in a typical run). The Gemini runs mostly don't hit a cache. Caching
cuts the token bill and leaves the per-turn time alone.

There is no cost column. The chat side's tokens come from `--trace`, but the
tool doesn't report Jev's, and an estimate beside a measured figure would
mislead. `../jev-vs-chat` has the cost comparison for the agent loop.

The arms get different inputs on purpose. Jev is handed the candidate fields
the browser already collected and answers constrained questions about them.
The chat model is handed the page and the tools and finds its own way. The
demo measures what each approach costs to reach the same two selectors.

Treat this as a demo: one page shape, one laptop and one network. The
statistically meaningful suites live in `../src`.
