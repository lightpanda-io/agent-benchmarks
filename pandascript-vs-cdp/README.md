# pandascript-vs-cdp

Benchmark: PandaScript replay (`lightpanda agent script.js`) vs the same tasks
written for Puppeteer and Playwright over CDP, driving `lightpanda serve` and
headless Chrome. Live-site runs against news.ycombinator.com, allbirds.com,
and apnews.com, plus a local login fixture.

Contents:

- `scripts/` — the benchmarked task scripts, one per driver
  (pandascript / puppeteer / playwright)
- `harness/` — `bench.py` (round-robin benchmark orchestrator), `ab.py`
  (variant A/B runner), `report.py` (aggregation), `plot.py` (figures),
  `browsers.py` (browser lifecycle), `login_fixture.py` (local login server),
  `har_capture.js` (HAR capture for request-profile comparisons),
  `memprobe.py` (peak-PSS-over-process-tree memory probe)
- `results/` — raw per-run JSONL + meta for every published dataset
  (`stock-*` = published tables, `pub-*` = `--http-cache-dir` config,
  `full-*` = investigation runs)
- `figures/` — per-run distribution plots (regenerate:
  `uv run --with matplotlib python harness/plot.py stock`)
- [`RETAIL-INVESTIGATION.md`](RETAIL-INVESTIGATION.md) — why the first retail
  run lost to Chrome, the investigation, and what came out of it
  (lightpanda-io/browser#2886, the cache matrix, the CDP-flake resolution)

## Tasks

- **scrape** — HN front page → top-5 stories → each item page → top-3 comments
  (6 serial page loads). `scrape_par` is the PandaScript-only parallel variant
  (one `Page` per story, `Promise.all`).
- **login** — HN login form → fill credentials → Enter → read karma from the
  profile page. Needs a throwaway account. Caution: benchmark-frequency logins
  trip HN's "Validation required" captcha per IP; once tripped, even GET /login
  serves the validation page for a while.
- **retail** — price monitoring on allbirds.com: collection page → first 3
  product cards (name, color, url) → each product page → price
  (`og:price:amount`) + available sizes (4 page loads, live site).
- **news** — media monitoring on apnews.com: section page → first 3 article
  links → each article → headline + first paragraphs (4 page loads, live,
  ad/tag-heavy).
- **login_fx** — the same flow against a local fixture
  (`harness/login_fixture.py`) that mimics HN's login markup and selectors.
  Zero network noise, no captcha risk: measures pure driver-stack overhead.
  The harness starts/stops the fixture server itself and injects fixture
  credentials.

## Configurations

`pandascript`, `puppeteer-lightpanda`, `puppeteer-chrome`,
`playwright-lightpanda`, `playwright-chrome`. CDP scripts *connect* to a
browser the harness launched (`BROWSER_WS` env: `ws://` for lightpanda,
`http://` for Chrome); the harness owns the browser lifecycle so cold timing
can bracket it.

## Modes

- **cold** — timer covers browser launch + CDP-ready poll + `node script.js`
  (kill outside the timer). PandaScript: the single `lightpanda agent` command.
- **warm** — browser pre-launched and held; timer covers a fresh
  `node script.js` (still pays Node startup + CDP connect). PandaScript has no
  warm/cold split; its warm number is its cold number.

Executions are interleaved round-robin (one execution of every config per
rotation) so live-site latency drift hits all configs equally. Report medians
+ IQR via `report.py`. Per-run shape validation discards bad runs; any
"Validation required" (captcha) response aborts a login benchmark outright.

## Runbook

```bash
cd ../../browser && make build          # ReleaseFast — required, debug skews everything
cd ../benchmarks/pandascript-vs-cdp
npm ci

export LPD_PATH=$(realpath ../../browser/zig-out/bin/lightpanda)

# scrape: 2 warmup + 20 measured rotations, 3 s pacing
uv run python harness/bench.py --task scrape --mode cold --runs 20 --warmup 2 --pace 3
uv run python harness/bench.py --task scrape --mode warm --runs 20 --warmup 2 --pace 3
uv run python harness/bench.py --task scrape_par --mode cold --runs 10 --warmup 1 --pace 3

# retail (live allbirds.com): same rotation scheme
uv run python harness/bench.py --task retail --mode cold --runs 20 --warmup 2 --pace 3
uv run python harness/bench.py --task retail --mode warm --runs 20 --warmup 2 --pace 3

# news (live apnews.com): reduced rotations
uv run python harness/bench.py --task news --mode cold --runs 12 --warmup 2 --pace 3
uv run python harness/bench.py --task news --mode warm --runs 12 --warmup 2 --pace 3

# login (live HN): throwaway account, ≥45 s between logins (captcha risk), small n
export LP_HN_USERNAME=... LP_HN_PASSWORD=...
uv run python harness/bench.py --task login --mode cold --runs 5 --warmup 1 --pace 45

# login_fx (local fixture): no creds or pacing needed
uv run python harness/bench.py --task login_fx --mode cold --runs 20 --warmup 2 --pace 1
uv run python harness/bench.py --task login_fx --mode warm --runs 20 --warmup 2 --pace 1

# memory: peak PSS over each config's full process tree, cold runs
uv run python harness/memprobe.py --tasks scrape,retail,news,login_fx --iters 5 --pace 5

uv run python harness/report.py results/<dir> [results/<dir> ...]
```

Each results dir gets `raw.jsonl` (one line per execution), `meta.json`
(versions, kernel, CPU governor), and the report prints median/p25/p75/min/max
plus median browser launch-to-ready for cold runs.

## Reruns and site blocking

Live-site benchmarking from one IP has a finite budget. Two observed failure
modes, so you recognize them:

- **HN login**: a handful of login attempts in quick succession trips a
  per-IP reCAPTCHA wall (even `GET /login` then serves the challenge page).
  That's why login timing uses the local fixture.
- **Storefront bot protection**: after several days of benchmark campaigns,
  allbirds.com began serving sub-second empty/challenge pages (0 product
  cards) to lightpanda-engine traffic specifically, while Chrome configs in
  the same rotations passed — engine-fingerprint level, not just IP. The
  validity gate catches it (`expected 3 products, got 0` with sub-second
  timings, consecutively). It cleared after a few hours of no traffic.

If you see consecutive same-config validity failures with sub-second run
times, stop the run — the data is garbage and continuing is impolite.

## Fairness notes

- Chrome's profile dir is pre-created once, untimed (`--no-first-run`), then
  reused — slightly generous to Chrome's cold number.
- Fresh browser context per warm run in both drivers; no cache clearing
  anywhere (symmetric).
- `LIGHTPANDA_DISABLE_TELEMETRY=true` on every lightpanda invocation.
- Distinct ports per CDP config in warm mode so held instances share nothing.
