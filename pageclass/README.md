# pageclass

A labelled corpus of real pages, and the score that decides whether the
`pageClass` model arm survives.

The bar, set before any of this was built: **the model arm has to beat the rules
on pages the HTTP status did not settle.** That subset is the only ground a rule
cannot reach, and it is the only place the comparison means anything. Everything
here exists to produce that one number honestly.

Needs a `lightpanda` built from a branch carrying the `pageClass` tool. The only
dependency is the `lightpanda` client itself, which drives the MCP server and
resolves the binary — `uv sync --group pageclass` once.

```bash
export LIGHTPANDA_BIN=/path/to/an/immutable/copy/of/lightpanda

# 1. harvest. No tokens: --judge is off, so this records only what the rules said.
uv run --group pageclass python pageclass/harvest.py --out pageclass/corpus/v1.jsonl

# 2. label what the harvest could not settle.
uv run --group pageclass python pageclass/review.py pageclass/corpus/v1.jsonl

# 3. the rules alone.
uv run --group pageclass python pageclass/score.py pageclass/corpus/v1.jsonl

# 4. the same urls again, with the model. This one costs tokens.
uv run --group pageclass python pageclass/harvest.py --judge \
    --out pageclass/corpus/v1-judged.jsonl
uv run --group pageclass python pageclass/score.py \
    pageclass/corpus/v1.jsonl pageclass/corpus/v1-judged.jsonl
```

`harvest.py` appends and resumes, so an interrupted run keeps what it got and a
re-run only fetches what is missing. `review.py` writes a separate
`.labels.jsonl` and never rewrites the corpus, for the same reason.

`--binary` overrides the client's usual resolution order (explicit path, then
`LIGHTPANDA_BIN`, then the bundled copy, then `PATH`, skipping the package's own
console script).

Copy the binary somewhere immutable before a campaign: an editor build will swap
`zig-out/bin/lightpanda` underneath a running harvest, and a debug build skews
nothing here (no timing is measured) but does change what the parser produces.

## What each probe is worth

Three probes per site, and **only the two manipulated ones carry a proposed
label**:

| probe | url | proposes |
| --- | --- | --- |
| `home` | `https://<host>/` | nothing |
| `uuid` | `https://<host>/<uuid4>` | `not_found` |
| `search` | a known search URL with a nonsense query | `empty` |

A homepage proposes nothing on purpose. **A homepage's class is a property of
the site, not of our request** — `flights.google.com` serves a consent wall at
200, so "homepage means content" is simply false, and auto-labelling it
`content` would hand the rules a free win on precisely the pages they get
wrong.

The two manipulations propose because we control the input, not because the
answer is guaranteed: a uuid path behind a bot wall returns a challenge, not a
404. So a proposal is auto-accepted only when the status corroborates it — a
hard 404 or 410 *and* a matching verdict. Everything else waits for review. A
soft 404 returns 200 and is indistinguishable from content to the rules, which
is the whole point of the exercise, so it is confirmed by eye or not at all.

## The labelling guide

Read this before reviewing, and do not improvise: two reviewers who resolve the
overlaps differently produce a corpus that cannot measure anything.

- **`content`** — the page carries what the URL asked for. A search *homepage*
  is content: it is the page you asked for.
- **`empty`** — a listing or search surface rendered with zero items. The
  surface is right; there is nothing on it.
- **`not_found`** — the page says the thing itself does not exist.
- **`empty` vs `not_found`**, the overlap that bites: if the page renders the
  *listing surface* around zero results, it is `empty`; if it replaces the
  content with a "no such thing" message, it is `not_found`. So
  `quotes.toscrape.com/tag/<absent>/`, which renders the tag page with "No
  quotes found!", is **`empty`** — the tag surface rendered, it just had
  nothing. A `/<uuid4>` path that returns a styled "page not found" body at 200
  is `not_found`.
- **An obstruction beats the content behind it.** If a consent wall, login wall
  or challenge stands over a live page, label the obstruction — a caller has to
  clear it first. That is also what the model is told.
- **`consent_wall`** covers cookie, consent, age and region gates.
- **`login_required`** means anonymous access cannot see the content, not that
  a sign-in link exists somewhere on the page.
- **`bot_blocked`** is a refusal with no challenge offered; **`captcha`** is a
  refusal with one. If a challenge is present, it is `captcha`.
- **`loading`** means the page had not settled, not that it is slow. Expect
  almost none of these.

Nothing here tries to defeat a wall. A challenge or a `cf-mitigated` header is
read so the refusal can be *recorded*, which is what makes those rows worth
having.

## Reading the score

`score.py` prints three things and the third is the one that matters:

1. **arm D, every labelled row.** Flattered by the status-settled rows, where a
   rule is right by construction. Do not quote it.
2. **arm D, rows the status did not settle.** The honest baseline.
3. **the comparison**, when a judged corpus is given: both arms over the rows
   present in both, plus the share of rows that reached the model at all,
   median latency, mean input tokens, and cost at list price.

It also reports how often a sub-0.70 confidence was wrong. That is the number a
cascade would threshold on, and it is the claim about calibration that is worth
testing rather than repeating.

Rows that are unlabelled, or that errored, are excluded from every figure and
counted separately. A run that says "N unlabelled" has not been scored yet.

## Probing the request shape

`judgePage` sends one page per request. Judging a corpus that way is one round
trip per row, so the question is whether many pages belong in one request —
System One ingests the state once and answers every question against it, and the
ids are the caller's, so `class:p7` and a state of 25 pages is a legal request
today with no client change.

Two probes, stdlib only, no browser, no project environment:

```bash
export TYPESAFE_API_KEY=...   # not the gateway: it 503s on bodies this size

uv run --no-project python pageclass/probe_pages.py --dry-run
uv run --no-project python pageclass/probe_pages.py \
    --sizes 1,5,10,25 --out pageclass/corpus/probe-pages.jsonl
uv run --no-project python pageclass/probe_questions.py \
    --pages 25 --asked 1,5,10,25 --out pageclass/corpus/probe-questions.jsonl
```

`probe_pages.py` grows the state and holds the questions at four, isolating
ingest. `probe_questions.py` holds the state and grows the questions, which is
TypeSafe's "adding questions barely changes the response time" at the scale this
would need. `--dry-run` prints the configurations and their sizes without
sending anything.

**What the numbers mean.** Fit `latency = A + B * tokens`. Sequential over N
pages costs `N * (A + B * t)`; one batched request costs `A + B * (N * t)`. The
token term is identical, so **batching saves exactly `(N-1) * A` and nothing
else** — and N concurrent requests beat both, finishing in about one request's
latency. So the whole case rests on the size of `A`, which only `probe_pages.py`
can measure: `probe_questions.py` never shrinks the state, so its intercept is
extrapolation and it says so rather than quoting a saving.

Read the `fit d%` column, not a cost per token: with a fixed per-request cost,
ms-per-token falls as the request grows even when the cost is perfectly linear,
so that ratio cannot tell linear from superlinear and the residual can. A
configuration far off the line means the cost is superlinear in size, and then
batching is a penalty and concurrency is the shape.

The connection matters as much as the shape. `judgePage` builds a
`typesafe.Client` per call and so pays a TLS handshake per judgement — 550 ms
against 234 ms pooled, measured. `probe_pages.py` reports that cold-minus-warm
delta first, because hoisting the client is a smaller change than batching and
recovers the same kind of time.

### What the first campaign found, 2026-09-30

Direct endpoint, `jev-1.13.0`, 43 unsettled rows from `v2.jsonl`, 3 repeats.

| | |
| --- | --- |
| fixed per request (`A`) | 205–229 ms |
| of which the TLS handshake | 55 ms |
| marginal (`B`) | 8–12 ms per 1k input tokens |
| per question | +1.4 ms, +148 input tokens |
| 25 pages in one request | 362 ms (322–391) |
| 25 pages one at a time | ~220 ms each, ~5.5 s |

**Ingest is nearly free and the round trip is almost the whole cost.** 13.5k
tokens of state buys about 150 ms. So batching 25 pages is ~15x faster than
sequential, and it is also **~16% cheaper**: 27.7k tokens for one 25-page
request against ~32.8k for 25 single-page ones, which repeat the per-request
scaffolding 25 times. Against 25 *concurrent* requests it is a wash on wall
clock — both land near one request's latency — and batching wins only on
holding one connection instead of 25, and on those tokens.

Asking about every page rather than one adds 96 questions, doubles the input
tokens and costs 134 ms, so the question axis is cheap in time and not in
tokens. Each question carries its own copy of the rules; moving the shared
rules into the state and referencing them per question is the obvious next
trim, and `probe_questions.py` measures it.

`probe_score.py` scores the answers the campaign already paid for: 23/25 agreed
with the per-page judgement, and against the human labels the batched arm went
22/24 where per-page went 20/24 — two rows either way, which is noise, not a
result. It is also not a clean A/B: the batched arm sees each page's text at
`result_text_budget` and the judged file was produced at `text_prefix_cap`.

**One page answered differently across identical repeats** (a lemonde 404, twice
`server_error` and once `loading`). `judgePage` disables retries on the grounds
that a calibrated decoder returns the same answer for the same state; on this
evidence that holds for 24 of 25 pages and not for the 25th. Worth re-checking
per-page before more is built on it.

**These probes settle latency, not accuracy.** Questions are answered in
isolation but share one state, so a batch can contaminate: the answer for `p7`
is produced with 24 other pages in view. Both probes record the `Judgment`
fields per page in their `--out` jsonl precisely so that can be scored against
`corpus/v2-judged.jsonl` — one page per request — without paying for the
judgements twice. Batching earns its place only if it holds the bar the model
arm itself had to clear.

## Known gaps

- **The text-budget curve is not here.** Sweeping how much page text the model
  needs would want either a knob on the tool to trim `text_prefix`, or the
  request rebuilt in Python — and rebuilding it duplicates the question text,
  which is exactly the drift the `validate_choice` copy in `ultrafast/` already
  demonstrates. Add the knob rather than the copy.
- **The probes copy the question text, which this file warns against.** They
  rebuild the request in Python, so `probe_common.py` carries its own copy of
  the prompts and class descriptions — byte-identical to `pageclass.zig` when
  written, and free to drift after. That is deliberate and temporary: a batching
  knob on the tool is the right home, and it is not worth adding before the
  probes say whether batching pays. If they say it does, the knob replaces the
  copy rather than joining it.
- **`server_error` cannot be sampled.** It is not summonable at scale without
  abusing someone, so the rules' 5xx path is covered only by the unit tests.
- **The site list is small and Europe-shaped.** Consent walls are
  over-represented from an EU address and `geo_blocked` is under-represented
  from anywhere. Say where a campaign ran.
