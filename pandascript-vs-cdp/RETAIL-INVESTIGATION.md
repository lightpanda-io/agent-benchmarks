# Why lightpanda lost to Chrome on the retail benchmark — investigation

*Companion document to the "PandaScript vs Puppeteer and Playwright" benchmark post. When the first run of the retail task had lightpanda losing to headless Chrome by ~46% warm, this is the investigation that followed — kept public because the numbers only mean something if you can see how we chased them. Harness, scripts, and raw per-run data are in this directory.*

**Date:** 2026-07-04 · **Site:** allbirds.com (Shopify, Vue-based theme) · **Task:** collection page → 3 product pages, extract name/price/sizes (4 loads)
**Starting point:** puppeteer→Chrome 4.12 s warm vs PandaScript 6.00 s vs CDP-on-lightpanda 6.76 s; gap scales with page weight (~78 ms/page on HN vs ~660 ms/page here). Plus 6/200 CDP-on-lightpanda failures.

## TL;DR

Two independent multipliers, both now quantified; neither is "the engine is slow":

1. **Missing feature-detection APIs push the theme onto its legacy loader.** `HTMLScriptElement.supports` and `DOMTokenList.supports` (relList) don't exist, so the theme's loader falls back to `fetch()`-based chunk loading: **676 requests instead of 305** for two pages, with shared modules like `vue.esm-bundler.js` fetched **9× per page by the page's own JS** (Chrome: 1×). lightpanda's own module map dedupes correctly — verified via debug logs (1 fetch through ScriptManager, 8 via `webapi/net/Fetch.zig`).
2. **No HTTP cache by default** (`--http-cache-dir` unset ⇒ cache layer not installed) turns every repeat fetch into a network round-trip. Chrome absorbs them in its cache.

A ~20-line diagnostic patch implementing the two `supports()` functions flips the theme onto the modern path (request count −55%, vue.esm 9×→1× per page). `--http-cache-dir` alone already measured **statistical parity with Chrome** (4.82 s vs 4.70 s, overlapping IQRs).

## Evidence chain

### 1. HAR comparison (Playwright recordHar, both engines, same 2-page flow)

| | requests | unique URLs | redundant | js | notes |
|---|---:|---:|---:|---:|---|
| lightpanda (stock) | 676 | 164 | 512 | 629 | vue.esm ×9/page |
| lightpanda (+supports patch) | 305 | 160 | 145 | — | vue.esm ×1/page |
| Chrome | 432 | 242 | 190 | 296 | incl. images/fonts/css lightpanda never fetches |

### 2. Attribution (debug build, `--log-level debug`, one collection-page load)

For `vue.esm-bundler.js`: 9 HTTP fetches; **1** logged `script queue ctx=module` (ScriptManager `preloadImport` — correctly deduped), **8** logged `msg="fetch"` from `webapi/net/Fetch.zig:88` — i.e. the page's JavaScript calling `fetch()`. The theme's loader, not the browser.

### 3. Feature probe (page-side evaluate)

| API | lightpanda stock | patched |
|---|---|---|
| `HTMLScriptElement.supports("module")` | **missing** → throws | true |
| `link.relList.supports("modulepreload")` | **missing** → throws | true |
| `caches` (CacheStorage) | absent | absent |
| dynamic `import()` | works | works |

Loaders (Vite legacy plugin pattern, Shopify themes) probe exactly these and fall back hard.

### 4. Flag A/B (live site, round-robin, 8 rotations, medians, Chrome ref in every rotation)

| variant | median | vs baseline |
|---|---:|---:|
| cache-warm (persistent dir) | 4.67 s | −22% |
| **chrome-ref** | **4.70 s** | −21% |
| **cache (fresh dir per run)** | **4.82 s** | −19% |
| conns (12 host / 64 total) | 5.72 s | −4% |
| baseline | 5.98 s | — |
| noiframes | 6.04 s | +1% |

`--http-cache-dir` alone = Chrome parity. Raw data: `results/full-ab-flags/`.

### 5. supports() patch timing (Chrome-anchored, 8 rotations per run)

| configuration | median | ratio vs Chrome ref (same run) |
|---|---:|---:|
| stock baseline | 5.98 s | 1.27× |
| supports() patch | 5.22 s | **1.17×** |
| stock + cache | 4.82 s | 1.03× |
| supports() + cache | 4.60 s | **1.03×** |

The patch alone closes ~40% of the gap (fewer round-trips); the HTTP cache subsumes most of the remainder (redundant fetches become cache hits either way). With the cache, lightpanda is at Chrome parity within noise regardless of the patch — but the patch still cuts real network traffic ~2× (676→305 requests), which matters for bandwidth, target-site load, and politeness. Raw: `results/full-ab-featdetect/`.

## The CDP flake (6/200 runs)

Reproduced in 3 iterations of a driver loop: puppeteer `DOM.resolveNode` → `UnknownNode`; playwright variant "Unable to adopt element handle from a different document". Serve debug log at the failure instant shows `Runtime.executionContextCreated` immediately followed by `executionContextsCleared` — the document was replaced mid-query while the driver held node handles (theme-initiated re-navigation, likely on the legacy loader path). Tested: with the supports() patch the flake **did not reproduce in 50 runs** (stock: reproduced at iteration 3; at the ~4% observed rate, 50 clean runs ≈ 87% confidence). The theme's legacy loader path was triggering the document replacement. A suspected residual robustness gap ("Chrome degrades softer on mid-query document replacement") was filed as [#2887](https://github.com/lightpanda-io/browser/issues/2887) and then **refuted by a synthetic repro**: a local page that `location.reload()`s 20–80 ms after load while the driver loops `$eval` hard-fails identically on headless Chrome (`Cannot find context with specified id`). Chrome's clean benchmark record existed only because the re-navigation happened exclusively on the legacy path. #2887 closed; the fix is [PR #2886](https://github.com/lightpanda-io/browser/pull/2886) (merged).

## Postscript: cache effect is workload- and path-dependent (2026-07-05)

Full matrix on the post-#2886 binary (`results/pub-*` vs `results/stock-*`, medians; controlled same-rotation probe confirmed the serve-on-HN direction):

| task | agent | serve (pptr/pw) |
|---|---:|---:|
| scrape (HN, asset-light) | −29…−31% | **+5…+13% regression** |
| retail (Shopify) | −10…−12% | −11% |
| news (ad-heavy) | −38…−41% | −37…−44% |

Same engine, same cache code: the serve path regresses only on the asset-light site (mostly cache-miss writes, no reuse — but the agent path gains there, so it's not just write overhead; context-scoped partitioning or revalidation behavior?). Worth a browser-team look before any enable-by-default decision. Published benchmark tables use stock config as primary with the full cache matrix as a labeled appendix.

## Postscript 2: supports() fix generality (2026-07-05)

Single-page HAR spot-check on two further modern storefronts (post-#2886 binary vs Chrome 150, same capture script):

| site | engine | requests | js requests | duplicate fetches | worst URL |
|---|---|---:|---:|---:|---:|
| gymshark.com | lightpanda | 49 | 20 | 2 | ×2 |
| gymshark.com | Chrome | 116 | 21 | 6 | ×2 |
| kitandace.com | lightpanda | 195 | 143 | 35 | ×17 |
| kitandace.com | Chrome | 274 | 156 | 30 | ×11 |

JS request counts within ~8% of Chrome's on both; duplicate profiles comparable across engines (kitandace's repeated URL repeats on Chrome too — page behavior). No trace of the pre-fix N×-module storm: the fix is platform-level, not Allbirds-specific.

## Bookkeeping finds for the browser team

- `--http-timeout` code default is **5000 ms** (`Config.zig:377`) while help text claims 10000 (`help.zon:320`).
- `.done` wait mode (`Runner.zig:224`) requires no pending macrotask + network idle — unreachable on pages with recurring `setInterval` polling (fetch/MCP default; a timeout trap on analytics-heavy sites).
- `load` gates on **async scripts** draining (`ScriptManager.zig:95`) — Chrome fires `load` without them; inflates time-to-load on tag-heavy pages. Not measured separately here (secondary to the two main causes).
- `CURLOPT_PIPEWAIT` unset: same-host bursts open up to 6 connections instead of coalescing onto one H2 connection. Not measured (conns A/B suggests small).

## Outcomes

1. **`HTMLScriptElement.supports` + `DOMTokenList.supports` implemented and merged** — [lightpanda-io/browser#2886](https://github.com/lightpanda-io/browser/pull/2886) (~20 lines, disproportionate real-world impact: every Vite/Shopify-style loader feature-detects these). Candidate for the same class of issue, still open: a `CacheStorage` presence-probe.
2. **The CDP flake is resolved by the same fix** — it was theme re-navigation on the legacy loader path (0/50 repro post-fix vs repro-at-3 stock). The suspected residual robustness gap was filed as [#2887](https://github.com/lightpanda-io/browser/issues/2887) and closed after a synthetic repro showed Chrome hard-fails identically on genuine mid-query document replacement.
3. **HTTP cache by default remains an open engine question** — worth ~10–40% on multi-page tasks against asset-heavy sites (see the cache-matrix postscript), but the measured serve-on-HN regression above argues for understanding that first.
