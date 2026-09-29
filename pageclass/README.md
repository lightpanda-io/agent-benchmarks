# pageclass

A labelled corpus of real pages, and the score that decides whether the
`pageClass` model arm survives.

The bar, set before any of this was built: **the model arm has to beat the rules
on pages the HTTP status did not settle.** That subset is the only ground a rule
cannot reach, and it is the only place the comparison means anything. Everything
here exists to produce that one number honestly.

Needs a `lightpanda` built from a branch carrying the `pageClass` tool, and
nothing from PyPI — the scripts are stdlib only.

```bash
export LIGHTPANDA_BIN=/path/to/an/immutable/copy/of/lightpanda

# 1. harvest. No tokens: --judge is off, so this records only what the rules said.
uv run --no-project python pageclass/harvest.py --out pageclass/corpus/v1.jsonl

# 2. label what the harvest could not settle.
uv run --no-project python pageclass/review.py pageclass/corpus/v1.jsonl

# 3. the rules alone.
uv run --no-project python pageclass/score.py pageclass/corpus/v1.jsonl

# 4. the same urls again, with the model. This one costs tokens.
uv run --no-project python pageclass/harvest.py --judge \
    --out pageclass/corpus/v1-judged.jsonl
uv run --no-project python pageclass/score.py \
    pageclass/corpus/v1.jsonl pageclass/corpus/v1-judged.jsonl
```

`harvest.py` appends and resumes, so an interrupted run keeps what it got and a
re-run only fetches what is missing. `review.py` writes a separate
`.labels.jsonl` and never rewrites the corpus, for the same reason.

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

## Known gaps

- **The text-budget curve is not here.** Sweeping how much page text the model
  needs would want either a knob on the tool to trim `text_prefix`, or the
  request rebuilt in Python — and rebuilding it duplicates the question text,
  which is exactly the drift the `validate_choice` copy in `ultrafast/` already
  demonstrates. Add the knob rather than the copy.
- **`server_error` cannot be sampled.** It is not summonable at scale without
  abusing someone, so the rules' 5xx path is covered only by the unit tests.
- **The site list is small and Europe-shaped.** Consent walls are
  over-represented from an EU address and `geo_blocked` is under-represented
  from anywhere. Say where a campaign ran.
