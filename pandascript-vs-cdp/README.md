# pandascript-vs-cdp

Benchmark: PandaScript replay (`lightpanda agent script.js`) vs the same tasks
written for Puppeteer and Playwright over CDP, driving `lightpanda serve` and
headless Chrome. Live-site runs against news.ycombinator.com,
outdoorvoices.com (allbirds.com, then eu.gymshark.com, in earlier datasets —
see "Reruns and site blocking"), and apnews.com, plus a local login fixture.

Contents:

- `scripts/` — the benchmarked task scripts, one per driver
  (pandascript / puppeteer / playwright / playwright-py / lightpanda-py /
  browseruse)
- `harness/` — `bench.py` (round-robin benchmark orchestrator), `ab.py`
  (variant A/B runner), `report.py` (aggregation), `plot.py` (figures),
  `browsers.py` (browser lifecycle), `login_fixture.py` (local login server),
  `har_capture.js` (HAR capture for request-profile comparisons),
  `memprobe.py` (peak-PSS-over-process-tree memory probe)
- `results/` — raw per-run JSONL + meta for the published dataset:
  `v3-*` = the post's timing tables (`-warm-agentcache` = pandascript warm
  runs with the persistent per-session cache dir), `memory` = the peak-PSS
  probe. Superseded datasets (the original stock-config tables, the cache
  A/B matrix, investigation runs, and the balanced-power-profile `v2-*`
  rerun) live in git history — see `RETAIL-INVESTIGATION.md` for what they
  established.
- `figures/` — per-run distribution plots (regenerate:
  `uv run --with matplotlib python harness/plot.py v3`)
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
- **retail** — price monitoring on a live storefront: collection page →
  first 3 product cards (name, url) → each product page → price + sizes
  (4 page loads, live site). Originally allbirds.com (datasets in git
  history); ported to eu.gymshark.com after allbirds began blocking
  lightpanda-fingerprinted traffic from our IP; ported again to
  outdoorvoices.com (v13) because gymshark's listing is an infinite scroll
  whose sentinel a layout-less browser reports visible on every observe, so
  lightpanda paginated to page 17 and loaded 4x the products Chrome did —
  a different task on each engine, not a comparison. Outdoor Voices renders
  the same 12 cards server-side on both.
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
`playwright-lightpanda`, `playwright-chrome`, `playwright-py-lightpanda`,
`playwright-py-chrome`, `lightpanda-py`, `browseruse-lightpanda`,
`browseruse-chrome`. CDP scripts *connect* to a
browser the harness launched (`BROWSER_WS` env: `ws://` for lightpanda,
`http://` for Chrome); the harness owns the browser lifecycle so cold timing
can bracket it.

The two Python legs (deps: `uv sync --group psbench` in the repo root):

- **playwright-py-\*** — Playwright for Python (pinned to the same minor as
  the Node `playwright-core` dep), line-for-line ports of the Node scripts,
  run as `python script.py` against the same harness-launched browsers.
  Same cold/warm semantics as the Node CDP legs; pays Python interpreter
  startup where Node legs pay `node` startup.
- **lightpanda-py** — the lightpanda Python package
  ([lightpanda-io/lightpanda-python](https://github.com/lightpanda-io/lightpanda-python),
  a sibling checkout via the `psbench` uv group): `Browser()` spawns its own `lightpanda mcp`
  sidecar and drives it over MCP HTTP, so like pandascript there is no
  harness-launched engine and cold timing covers the whole
  `python script.py` (interpreter + sidecar spawn + task). Cache flags reach
  the sidecar via `BENCH_LPD_ARGS` → `Browser(args=...)`; the binary comes
  from `LIGHTPANDA_BIN=$LPD_PATH`. The memory probe's session-id PSS sum
  covers interpreter + sidecar together. There is **no `scrape_par`
  variant**: the MCP server dispatches one request at a time, so a parallel
  port would not actually run its page loads concurrently.

The browser-use CLI legs (`scripts/browseruse/`, Python scripts piped into
the `browser-use` binary):

- **browseruse-\*** — the [browser-use](https://github.com/browser-use/browser-use)
  CLI (browser-harness). The script is fed on stdin; the CLI spawns a daemon
  that holds one CDP websocket to the harness-launched engine (its local mode
  only attaches to a desktop Chrome, so both legs get the engine via
  `BU_CDP_WS` / `BU_CDP_URL`). Cold runs wipe the daemon and its state dirs
  (`BH_HOME`, `BH_RUNTIME_DIR`) and, on Chrome, the profile — the CLI drives
  the default browser context, which sees the profile's disk cache — so the
  daemon spawn is inside the cold timer; warm runs reuse the held daemon.
  `BROWSER_USE_BIN` is required (e.g. `~/.local/bin/browser-use` from
  `uv tool install browser-use`): under `uv run` a bare `browser-use`
  resolves to the venv's 0.12.x agent CLI, which is not the same program.
  Scripts fence each navigation on `location.href` changing before any
  readiness wait, since the CLI's polling waits can otherwise read the
  previous document.

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

- **parallel** (`--parallel N`, either mode) — every execution runs N flows
  of the config at once; `ms` is the batch wall clock (first launch to last
  exit) and the per-flow records sit under `flows`. `--topology process`
  (default) gives each flow its own engine: N `lightpanda serve` / N Chrome
  processes, the protocol of the
  [demo crawler benchmark](https://github.com/lightpanda-io/demo/blob/main/BENCHMARKS.md#crawler-benchmark)
  behind the README's headline numbers. `--topology shared` connects all N
  flows to one engine (N tabs in one Chrome; N sessions on one
  `lightpanda serve`, whose default cap of 16 CDP clients the harness raises
  when needed). Cold launches the engines inside the timer; warm holds one
  per flow (or the one shared engine) across the phase. `memprobe.py` takes
  the same two flags and reports the batch's peak PSS and CPU. The one cell
  the harness skips is browseruse-chrome under `shared`: the CLI's daemons
  all drive the default context's first tab, so N of them on one Chrome
  fight over it (on `lightpanda serve` each CDP connection gets its own
  page, so that leg runs).

  Live sites under batches, from the `par` campaign (2026-09-05): gymshark
  throttles at N=4 (zero product cards on every engine), so retail is
  single-flow only; apnews takes N=4 with every engine 2-3x slower per flow;
  Hacker News takes N=4 cleanly, and at 8 and 16 its burst limiter rejects
  some *instant-start* flows (PandaScript, browser-use) with an empty front
  page and tarpits a few for 5 s, while Puppeteer/Playwright flows are
  staggered by node startup and mostly pass — the report counts a batch
  when at least half its flows passed and shows the pass rate. Warm mode
  holds N browsers per config for every config at once: at N=16 with all
  seven that is ~100 browsers with live pages and it OOM-killed a 30 GB
  machine, so warm stops at N=8 on live sites. PandaScript's `goto` rejects
  at its 10 s default timeout where Puppeteer/Playwright wait 30 s, which
  matters on apnews under load.

  The burst problem is PandaScript's own speed: sixteen `lightpanda run`
  processes send their first request within the same millisecond, so a
  site's limiter sees one burst (HN: 503 for a quarter of them, the rest
  queued for seconds), while the node/Chrome startup in front of the other
  drivers staggers their requests for free. The `pandascript-paced` config
  is the same replay with its processes started 30 ms apart, inside the
  batch timer, which is what a scraper's pool does: 16/16 clean flows on
  HN, batch 2.7 s instead of 5.1 s (76% of flows passing). The parallel
  scrape figure shows the paced run as its PandaScript line and omits the
  instant-start one. (`--http-nav-delay` does not help
  here: it spaces navigations *within* one process, and sixteen Pages in
  one process is 4x slower than sixteen processes anyway.)

  Why it exists: single-flow latency on live sites is network-bound and
  shows lightpanda at roughly 1.2–1.9× Chrome; the crawler's ~9× is a
  throughput number at 25-way parallelism on a 4-vCPU box, where Chrome
  saturates the CPU and lightpanda's ~18× lower CPU per page turns into
  wall clock. `--parallel` measures that regime here; `memprobe.py`'s
  `cpu_s` is the per-flow CPU cost that predicts it.

## Runbook

```bash
cd ../../browser && make build          # ReleaseFast — required, debug skews everything
cd ../benchmarks/pandascript-vs-cdp
npm ci

export LPD_PATH=$(realpath ../../browser/zig-out/bin/lightpanda)
export LPD_CACHE=1   # published config: --http-cache-dir on the lightpanda side
                     # (fresh dir per browser/process; pandascript warm keeps it
                     # for the session — see lpd_cache_flags in bench.py)

# scrape: 2 warmup + 12 measured rotations, 3 s pacing
uv run python harness/bench.py --task scrape --mode cold --runs 12 --warmup 2 --pace 3
uv run python harness/bench.py --task scrape --mode warm --runs 12 --warmup 2 --pace 3
uv run python harness/bench.py --task scrape_par --mode cold --configs pandascript --runs 12 --warmup 2 --pace 3

# retail (live storefront): same rotation scheme
uv run python harness/bench.py --task retail --mode cold --runs 12 --warmup 2 --pace 3
uv run python harness/bench.py --task retail --mode warm --runs 12 --warmup 2 --pace 3

# news (live apnews.com): same rotation scheme
uv run python harness/bench.py --task news --mode cold --runs 12 --warmup 2 --pace 3
uv run python harness/bench.py --task news --mode warm --runs 12 --warmup 2 --pace 3

# pandascript warm supplement (persistent per-session cache dir — the
# warm-state analogue of a held browser; the post's warm pandascript rows)
uv run python harness/bench.py --task scrape --mode warm --configs pandascript --runs 12 --warmup 2 --pace 3

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

Both launchers refuse a port that already has a listener: a browser leaked
by an earlier run keeps its port, and the readiness poll would otherwise
accept it as the one just launched, so every later run on that port would
measure a stale, warm browser (`ss -ltnp | grep :92` finds the culprit).

Each results dir gets `raw.jsonl` (one line per execution), `meta.json`
(versions, kernel, CPU governor), and the report prints median/p25/p75/min/max
plus median browser launch-to-ready for cold runs.

The puppeteer scripts also print a `BENCH_STEPS` line on stderr with the
elapsed time of every step (connect, newpage, each goto, each wait, each
evaluate, close); the harness stores it as `steps` on the run and `report.py`
prints per-step medians, which splits a flow into driver floor, navigation
(network + engine) and in-page work. `memprobe.py` records `cpu_s`
(utime+stime over the whole process tree, sampled) next to `peak_pss_mb`,
split into `cpu_engine_s` / `cpu_driver_s` for the CDP configs.

```bash
# throughput regime: 8 concurrent flows per execution, one engine per flow
uv run python harness/bench.py --task login_fx --mode cold --parallel 8 --runs 10 --warmup 2 --pace 1 \
    --configs pandascript,puppeteer-lightpanda,puppeteer-chrome
# same, all flows on one held engine (N tabs / N sessions)
uv run python harness/bench.py --task login_fx --mode warm --parallel 8 --topology shared --runs 10 --warmup 2 --pace 1 \
    --configs puppeteer-lightpanda,puppeteer-chrome
# peak memory and CPU of the whole 8-flow batch
uv run python harness/memprobe.py --tasks login_fx --parallel 8 --iters 5 --pace 2
# the full sweep (1/4/8/16, both topologies, memory, then live sites at N=4): run-parallel.sh
```

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
- Python legs include the interpreter in both wall time and PSS, exactly as
  the Node legs include `node`'s — symmetric per-stack overhead, not noise.
- `lightpanda-py` cold includes its sidecar spawn because that *is* its cold
  path; comparing it to CDP-leg cold (browser launch included) is
  like-for-like.
