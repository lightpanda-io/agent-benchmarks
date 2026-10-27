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
| `--timeout` | `300` | seconds per run |

The Jev arm needs `TYPESAFE_API_KEY` and the chat arm needs a key for its
provider. The fixture supplies its own throwaway credentials. `--site hn`
reads `$LP_HN_USERNAME` and `$LP_HN_PASSWORD` and passes them as `$LP_*`
placeholders, which resolve inside the browser, so the secrets stay out of the
model's context and the result text.

## Measured

Five runs per chat model on 2026-10-27, on one laptop, with lightpanda
`3108ce691` (the `jev-fallback` branch). Jev and the replay ran alongside
every chat model, so they have fifteen runs each. Every run signed in.

| arm | median | range | round trips | per round trip |
| --- | --- | --- | --- | --- |
| jev | 0.52s | 0.49–0.76s | 2 Jev calls | |
| claude-haiku-5-5 | 8.2s | 7.5–9.7s | 5 | 1.6s |
| gemini-3.5-flash-lite | 8.9s | 8.6–11.9s | 7–10 | 1.0s |
| gemini-3.8-flash | 19.1s | 17.9–24.7s | 8–11 | 2.1s |
| replay | 0.006s | 0.004–0.010s | none | |

Jev is 16x faster than Haiku, the fastest chat model, on the median. Its
slowest run was 10x faster than Haiku's fastest. Flash-lite has the quickest
round trips but needs more of them; Haiku batches its tool calls (all three
`nodeDetails` in one turn, both `fill`s in the next) and always finishes in
five.

Effort stays at the REPL default, `low`. That is the floor for
gemini-3.8-flash: it answers `minimal` with HTTP 400, and `none` sends no
thinking level, which leaves the model on its own default.

On the live HN page, Jev picks from 7 candidate fields and controls in 0.39s.
Its `submitSelector` comes back as
`form:has(input[type="submit"][value="login"]) > input`. HN serves a sign-in
form and a create-account form with identically named inputs, and telling them
apart is the hard part of this page.

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

`✓` means the run left the login page. The Jev and replay arms also report
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

## Caveats

The default page is a local fixture of HN's login markup (`input[name=acct]`
and `input[name=pw]` in a borderless table). HN rate-limits repeated logins
per IP, and a benchmark that signs in fifteen times per arm trips validation.
`--site hn` checks the real page by asking Jev which fields it would use, and
types nothing, so it is safe to repeat. `--site hn --live` signs in for real,
three times per repeat (once per arm). Use it to check the real page end to
end, and don't loop it to collect a median.

The chat arm's time goes on round trips. Haiku's five turns take 1.2 to 2.3
seconds each and write about 600 output tokens in total. Moving from
gemini-3.8-flash to Haiku cut the chat arm by more than half, and Jev is still
16x faster, because every chat model pays a round trip per turn.

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
