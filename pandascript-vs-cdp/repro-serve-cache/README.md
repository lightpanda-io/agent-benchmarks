# Repro: serve-path HTTP cache regression

`--http-cache-dir` makes the **agent** path much faster and the **serve**
(CDP) path much *slower* — on the same binary, same fixture, same cache
code. On the serve path, cache **hits are slower than misses**.

## Run

```bash
LIGHTPANDA=/path/to/lightpanda ./run.sh
```

Needs `python3`, `node`, and `puppeteer-core` (`npm ci` in the parent
directory). Takes ~30 s. Fully offline: the fixture is a local HTTP server
on port 9300.

## Workload

Six pages (`/site/1..6`), each including the **same three** script assets
(`/asset/0..2.js`), served with `Cache-Control: public, max-age=3600` and a
30 ms artificial server delay each — a minimal model of a multi-page site
with shared JS. The walk does `goto` × 6 (wait: load) and reports elapsed
wall time from inside the walk (process startup excluded).

## Observed (2026-07-17, 1.0.0-dev.8162+a59abc7f, linux x86_64)

| cell | trial 1 | trial 2 | trial 3 | |
|---|---:|---:|---:|---|
| agent nocache | 206 | 204 | 201 | |
| agent cache | 45 | 12 | 12 | cache works: warm hits ~17× faster |
| serve nocache | 210 | 212 | 210 | |
| serve cache | 1,059 | 1,226 | **1,228** | **~6× slower; warm hits slower than cold misses** |

The cache dir persists across the 3 trials of a cell, so trial 1 is a
cold cache (all misses + writes) and trials 2–3 are warm (all hits).
`agent` behaves exactly as expected; `serve` pays ~170 ms per page *on
hits* — consistent with a per-response wait on something cache-served
responses never deliver (network-event/timeout interaction in the CDP
layer?), not with cache I/O cost.

## Files

- `run.sh` — orchestrates all four cells (fixture + serve lifecycle included)
- `walk_agent.js` — the walk as a PandaScript replay script
- `walk_serve.js` — the same walk via puppeteer-core over CDP
- fixture: `../harness/load_semantics_fixture.py` (`/site/`, `/asset/` endpoints)
