# ultrafast

A [jev-ultrafast](https://github.com/browser-use/jev-ultrafast)-shaped browser
agent — one structured decision per step over an indexed table of controls —
run over a 2×2 of **browser × decider**, from a plain Python script.

The point of the demo is the top-left cell. Lightpanda here is the **stock
binary**: no Jev code in the browser, no injected snapshot script, nothing to
build. The whole observation is one `tree` call through
[lightpanda-python](https://github.com/lightpanda-io/lightpanda-python), because
the semantic tree already carries what an agent policy needs — role, accessible
name, current value, checkbox state, select options with the selected one
marked. Chrome is the control, observed with browser-use's own `snapshot.js`.

|            | jev            | chat            |
| ---------- | -------------- | --------------- |
| lightpanda | **the demo**   | decider control |
| chrome     | browser control| browser + decider control |

`B−A` is the decider effect, `C−A` is the browser effect, and `D` is there so a
Jev win cannot be mistaken for a Chrome-vs-Lightpanda effect.

## Run it

```bash
cd ..                                   # benchmarks/
uv sync --group psbench --group ultrafast

export LIGHTPANDA_BIN=/path/to/an/immutable/copy/of/lightpanda
export AI_GATEWAY_API_KEY=...           # reaches Jev, the chat decider and the text helper
# or, better for link-dense pages:
# export TYPESAFE_API_KEY=...           # the direct System One endpoint

uv run python ultrafast/demo.py                    # all four arms on one task, once
uv run python ultrafast/run.py --task hotel --browser lightpanda --policy jev -v
uv run python ultrafast/bench.py --tasks hotel,quotes,wikipedia,hn --runs 6 --warmup 1
uv run python ultrafast/report.py ultrafast/results/<stamp>
```

`demo.py` is the showable one. `bench.py` is the same runs, interleaved and
repeated, and is what any quoted number should come from.

Build the binary `make build` (ReleaseFast) and **copy it somewhere immutable**
before a campaign: a debug build skews every browser number, and an editor build
will swap `zig-out/bin/lightpanda` underneath a running campaign. `bench.py`
records the sha256 it actually ran.

Knobs, all environment variables:

| var | default | |
| --- | --- | --- |
| `LIGHTPANDA_BIN` | — | required; the package ships no binary in a source checkout |
| `TYPESAFE_API_KEY` / `AI_GATEWAY_API_KEY` | — | the Jev channel, TypeSafe first |
| `JEV_MODEL` | `jev-latest` / `typesafe-ai/jev` | per channel |
| `CHAT_API_KEY` / `CHAT_BASE_URL` / `CHAT_MODEL` | gateway / `google/gemini-3.8-flash` | the chat decider |
| `TEXT_MODEL_API_KEY` / `TEXT_MODEL_BASE_URL` / `TEXT_MODEL` | gateway / `inception/mercury-2.5` | the TYPE_TEXT helper |
| `ULTRAFAST_MAX_OFFERED` | `120` | elements offered per observation |

One gateway key runs all four arms. Without a `TEXT_MODEL` key, `TYPE_TEXT` is
dropped from the action space entirely — nothing is ever typed from a literal
lifted out of the goal.

## The tasks

| id | where | shape |
| --- | --- | --- |
| `hotel` | local fixture | TYPE_TEXT → CLICK → SELECT → CLICK a checkbox → open a result |
| `quotes` | quotes.toscrape.com | SELECT → server postback → SELECT → submit |
| `wikipedia` | en.wikipedia.org | TYPE_TEXT → search → land on the article |
| `hn` | news.ycombinator.com | one CLICK, out of ~120 offered elements on a link-dense page |

`hotel` is the headline: browser-use's own `static/fixture.html`, vendored
unmodified and served from loopback, so both engines see identical bytes and the
number is not network noise. It is the same workflow upstream published at
1.896 s. The other three are live, for credibility, and carry live variance.

Every task is judged by a checker in `tasks.py` that runs after the clock stops,
against a fresh unclipped read of the page — `document.body.innerText` on Chrome,
`markdown` on Lightpanda. `DONE` is the model's opinion and never counts as
evidence. `hn` resolves the expected story id by fetching the front page itself,
before the run, so the answer never comes from the browser under test.

## Results (`results/v5-direct`, `results/v6-direct-jev`, `results/fleet-direct`)

All campaigns ran one binary: **ReleaseFast, main `7f4387b9c`**, sha256 `c2ec817c…`,
copied read-only into `.bench-bin/` so an editor build cannot swap it
mid-campaign. Use `TYPESAFE_API_KEY` — see the endpoint note below.

### The action space is the browser's whole effect

| task | lightpanda offers | its decision | chrome offers | its decision | ratio |
| --- | ---: | ---: | ---: | ---: | ---: |
| hotel | 8 | 278 ms | 8 | 272 ms | 1.0x |
| quotes | 6 | 282 ms | 6 | 279 ms | 1.0x |
| wikipedia | 120 | 390 ms | 39 | 296 ms | 1.3x |
| hn | 120 | **902 ms** | 34 | 286 ms | **3.2x** |

Identical tables give identical decision times to within 6 ms. A 3.5x bigger
table costs 3.2x the decision time, and 59k input tokens against 27k on
wikipedia. Having no viewport to clip against costs **both tokens and time** —
an earlier version of this README said tokens only, which was gateway noise
hiding the effect.

Run times (raw, nothing subtracted), jev arms, two campaigns:

| task | lightpanda | chrome |
| --- | ---: | ---: |
| hotel | 3.4 / 3.5s | 3.6 / 3.7s |
| quotes | **2.0 / 2.0s** | 2.8 / 2.8s |
| wikipedia | 4.3 / 9.8s | 20.4 / 4.4s (live, wanders) |
| hn | 2.7 / 2.8s | 2.1 / 1.9s |

### Decider

Jev answered in 266–902 ms against 2226–3672 ms for one chat turn, in every
paired cell of every campaign. The chat model's own latency has swung 5x across a
day (fixture runs of 21.2s, 51.7s, 105.5s), so quote per-decision ranges, never a
single multiple.

### Fleet (`fleet.py`)

Per flow: lightpanda **90 MB / 0.76 s CPU**, chrome **1,852 MB / 2.3 s CPU** —
21x the memory, 3x the CPU, flat from N=1 to N=64. On 19.3 GB free that is ~214
concurrent agents against ~10. At N=128 lightpanda completed **125 of 128 with
zero retries**; the N=128 peak-RSS figure under-reports because flows stagger and
a 100 ms sampler cannot walk 128 process trees fast enough.

### Use the direct endpoint

`AI_GATEWAY_API_KEY` reaches Jev through the Vercel gateway, which returns 503 on
large bodies — probabilistically, and it 0/3'd the wikipedia+lightpanda cell in
two campaigns. Measured 2026-09-25: the direct `api.typesafe.ai` accepts 250
options (the documented max, 36.7 KB) and a 52 KB body, all 200 in ~1.1 s. Across
72 direct runs: **1 retry total**, against 40–641 per gateway campaign.

`net.post_json` still takes a `shrink` callback that re-renders a rejected request
smaller (70%, 49%, 34% of the table, recorded as `request_shrinks` /
`min_request_scale`). On the direct endpoint it never fires. It exists so a
gateway run degrades instead of dying.

The four gateway-era campaigns are kept in `results/{v1-noretrycol,v2,v3,v4}` for
comparison; **do not mix their seconds with these**.

## What is shared and what is not

Shared: the loop (`ultrafast/agent.py`), the bound on the table
(`ultrafast/table.py`), the state JSON, the questions, both deciders, the text
helper, and the clock. Timing starts at the first decision after the initial
observation and ends at the accepted `DONE` — upstream's definition, so the
numbers sit next to theirs. Browser launch, the initial navigation and
verification are outside it.

Not shared, and deliberately so: **the observer**. Upstream keeps its table small
for free by capturing only elements whose rect centre lies inside a 1120×780
viewport. Lightpanda has no layout — probed live, Wikipedia's search input
reports `{x:20, y:1185, w:5, h:5}` against `innerHeight: 1080`, a flat box at a
node-ordinal position — so a viewport filter over it would be arbitrary and
`snapshot.js` cannot be ported. Each engine is therefore observed the way it is
meant to be, and both tables then pass through the same `bound()`: dedupe on
`(role, label)`, cap at `MAX_OFFERED`, truncate labels to 120 bytes, and report
`elements_omitted` so the decider does not read a truncated list as the whole
page. Unnamed controls are exempt from the dedupe — a Hacker News page carries
one nameless upvote arrow per row and one per row is the point of them.

Three consequences worth stating rather than burying:

- Lightpanda offers no `SCROLL_UP`/`SCROLL_DOWN`. With no viewport there is
  nothing below the fold; the whole document is already in the table. Chrome's
  action space has them. The arms therefore do not have identical action spaces.
- Lightpanda pays **two** calls per observation (`tree`, plus one `evaluate` for
  the page URL and the freshness token) where Chrome's injected snapshot returns
  all of it in one. `report.py` counts them.
- The `elems` column will usually be larger on Lightpanda, and `elements_omitted`
  smaller, for the same page.

## Caveats to keep attached to any number here

- Four tasks and a handful of repeats is a demo, not a benchmark. The benchmark
  is GAIA/AssistantBench in `../src/agent_benchmarks`.
- The chat arm gets exactly what Jev gets — same table, same `recent_actions`
  window, same NEXT_ACTION/TARGET text — and answers in one turn. It does **not**
  carry a growing conversation, unlike `../jev-vs-chat`'s chat arm, so its token
  bill here is much lower than a conversational agent's would be.
- The two deciders are not retried on equal terms, on purpose. An invalid Jev
  answer ends the step: a calibrated decoder returns the same answer for the same
  state, so re-asking would only burn tokens. A chat model does not, so a
  malformed reply gets exactly one more turn before the run dies — otherwise the
  control arm loses runs to JSON formatting rather than to decision quality.
  Transport errors and 5xx are retried on both.
- Costs are list price. Jev bills input tokens only and output is free; the
  Vercel promo currently bills zero, so the figure is what it would cost.
- **The Vercel AI Gateway 503s on large System One bodies**, probabilistically
  rather than at a fixed size: the same 8.5 KB request has been seen to return
  200 and then 503, and ~39 KB fails reliably. That hits the link-dense tasks
  (`wikipedia`, `hn`) hardest, and the retry wait sits inside the clock.
  `report.py` prints the retry count per cell. Use a direct `TYPESAFE_API_KEY`
  for anything you intend to publish.
- Live sites move. `hn`'s front page changes between rotations, which is why the
  expected story is resolved per run; `wikipedia` and `quotes` are stable but
  network-bound.
- Jev has no chain of thought and no navigation operation. A task that needs
  `goto`, `press` or `extract` is out of scope, not a fair comparison.

## Vendored from browser-use/jev-ultrafast (MIT)

`ultrafast/snapshot.js` and `fixture/index.html` are unmodified upstream files;
`ultrafast/prompts.py`'s `NEXT_ACTION`, `TARGET` and `TEXT_VALUE` are upstream
text. See `VENDOR-LICENSE-jev-ultrafast`. The Zig port of the same loop *into*
the browser lives on the `policy-jev` branch — this demo is the argument that it
does not have to be there.
