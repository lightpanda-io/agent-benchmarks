# scraper-guard, step 0

Throwaway scripts and data from the 2026-09-30 smoke test for "a scraper that
knows it broke": can a `noul` tell a correct extracted product record from a
plausibly wrong one? Recovered from `/tmp/claude-jev-brief/`, which was one
reboot from losing all of it. **Kept for the corpus, not as a harness.** A real
harness belongs beside `pageclass/`, built from what these prove.

## What the smoke found

A decoy price that is genuinely on the page is what selector drift produces, and
on those the rule "does this value appear as money in the page text" catches
none, because the wrong number is on the page too. Jev separated them cleanly
(AUC 1.000 on two runs, decoys above and below the live price). 12 records from
mediamarkt speakers, one site and one category, so the separation is clean and
the sample is not.

Mutating a price arithmetically instead (x1.4, another page's price) produces a
number that is *not* on the page, which flatters that rule to 0.975 and nearly
became the headline. Never mutate arithmetically.

State size decides the outcome: records alone AUC 0.555, 6 KB of page text
0.833, full page 1.000.

## The files that matter

`sitemap.jsonl` is the corpus screen and the reason this directory was rescued:
78 product pages over 26 retailers, reached through `robots.txt` to sitemaps
because no collection grid renders. The verdict key is **`v`**, not `verdict`:

| `v` | n | meaning |
| --- | --- | --- |
| `blank` | 29 | the page returned no usable markdown |
| `no_oracle` | 28 | rendered, but no JSON-LD to check an answer against |
| `USABLE` | 16 | live price and a decoy both present in the text the model sees |
| `price_not_in_text` | 4 | price drawn in JavaScript, in the HTML and in no text |
| `no_decoy` | 1 | only one money value on the page |

The 16 usable rows span 7 hosts: mediamarkt.es 3, argos.co.uk 3,
sportsdirect.com 3, mediamarkt.de 2, saturn.de 2, verkkokauppa.com 2,
adorama.com 1. Capping at 40 pages per host gives about 264 rows with no host
above 15%, which clears n >= 100 with real spread. Do not quote the raw
extrapolation from these pools: argos's sitemap is 50k urls, so a per-host rate
times pool size reads absurdly high.

Electronics retailers server-render prices. Direct-to-consumer storefronts draw
them in JavaScript, which is what `price_not_in_text` and most of `blank` are.
Screening on raw HTML instead of rendered text overstates usability by 2x,
because Shopify embeds the whole product JSON in a `<script>` block.

`compat.jsonl` is the earlier, superseded screen over Shopify hosts (43 rows,
verdicts `DECOY` / `no_oracle` / `oracle_no_decoy`, no `USABLE` key at all).
`decoys.jsonl` and `decoys-judged.jsonl` hold the 12 scored records;
`lowdecoy-judged.jsonl` is the same with decoys below the live price.

## Two things worth handing to the browser team

`decathlon.es` and `decathlon.co.uk` return exactly 448 and 457 bytes of
markdown, `screwfix` 532: reproducible reduced cases for a blank render.

`outdoorvoices.com` product pages give `innerText` 583 bytes against
`textContent` 42,567. The DOM is populated and nearly everything computes as
hidden, which smells like a visibility gap rather than missing JavaScript.
