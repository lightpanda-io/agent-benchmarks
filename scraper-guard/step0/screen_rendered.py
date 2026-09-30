#!/usr/bin/env python3
"""The screen that matters: is the sale price SERVER-RENDERED, i.e. present in
the HTML Lightpanda receives? products.json gives url + both prices for free;
one html read per page decides it. No listing, no JSON-LD, no markdown."""
import httpx, json, re, sys, time
from lightpanda import Browser, ToolError

HOSTS = """allbirds.com brooklinen.com rothys.com vessi.com thursdayboots.com tentree.com
kotn.com outdoorvoices.com ridge.com drsquatch.com peakdesign.com hydrojug.com
chubbiesshorts.com untuckit.com mackweldon.com birddogs.com tecovas.com marinelayer.com
fahertybrand.com buckmason.com taylorstitch.com greats.com olukai.com xeroshoes.com
nisolo.com harrys.com ritual.com athleticbrewing.com magicspoon.com kettleandfire.com
carawayhome.com materialkitchen.com madeincookware.com fromourplace.com
hedleyandbennett.com bokksu.com monos.com nomadgoods.com pelacase.com manscaped.com
bulletproof.com foursigmatic.com liquiddeath.com drinkolipop.com""".split()
C = httpx.Client(timeout=20, follow_redirects=True,
                 headers={"User-Agent": "Mozilla/5.0 (compatible; lightpanda-research)"})
PER_HOST = 2

def forms(v):
    v = float(v)
    f = {f"{v:.2f}", f"{v:g}"}
    if v == int(v): f |= {str(int(v)), f"{int(v)}.00"}
    return f

def in_html(v, html):
    return any(re.search(r"(?<![\d.])" + re.escape(x) + r"(?![\d])", html) for x in forms(v))

def sale_items(host):
    try:
        r = C.get(f"https://{host}/products.json", params={"limit": 250})
        if r.status_code != 200:
            return None, f"http {r.status_code}"
        ps = r.json().get("products")
        if ps is None:
            return None, "no products key"
    except Exception as e:
        return None, f"{type(e).__name__}"
    out = []
    for p in ps:
        v = (p.get("variants") or [{}])[0]
        try:
            live, was = float(v["price"]), float(v["compare_at_price"])
        except (KeyError, TypeError, ValueError):
            continue
        if was > live:
            out.append({"url": f"https://{host}/products/{p['handle']}",
                        "live": live, "was": was})
    return out, None

def main():
    pools, verdicts = {}, []
    todo = []
    for h in HOSTS:
        items, err = sale_items(h)
        if err or not items:
            print(f"{h:24s} products.json: {err or 'no sale variants'}", flush=True)
            continue
        pools[h] = len(items)
        todo.append((h, items[:PER_HOST]))
        print(f"{h:24s} {len(items):4d} sale variants", flush=True)
    print(f"\n-- browser screen: {sum(len(i) for _, i in todo)} pages --\n", flush=True)
    with Browser(binary=sys.argv[1]) as b, b.new_session() as p:
        for host, items in todo:
            for it in items:
                try:
                    raw = p.html(url=it["url"], max_bytes=900000, timeout=30000)
                    html = raw if isinstance(raw, str) else json.dumps(raw)
                except ToolError as e:
                    verdicts.append({"host": host, "v": "err", **it})
                    print(f"  {host:24s} ERR {str(e)[:44]}", flush=True); continue
                lv, wv = in_html(it["live"], html), in_html(it["was"], html)
                v = "BOTH" if (lv and wv) else ("live_only" if lv else ("was_only" if wv else "neither"))
                verdicts.append({"host": host, "v": v, "bytes": len(html), **it})
                print(f"  {host:24s} {v:10s} {len(html):7d}b live={it['live']} was={it['was']}", flush=True)
                time.sleep(0.8)
    with open(sys.argv[2], "w") as f:
        for r in verdicts: f.write(json.dumps(r) + "\n")

    print("\n" + "=" * 76)
    print(f"{'host':24s} {'pool':>5s} {'BOTH':>5s}/{'seen':<4s}  est. usable decoy pages")
    total = 0
    for host in sorted(pools):
        vs = [r for r in verdicts if r["host"] == host]
        if not vs: continue
        both = sum(1 for r in vs if r["v"] == "BOTH")
        est = round(pools[host] * both / len(vs))
        total += est
        mark = "  <= USABLE" if both else ""
        print(f"{host:24s} {pools[host]:5d} {both:5d}/{len(vs):<4d}  {est:5d}{mark}")
    from collections import Counter
    print(f"\nverdicts: {dict(Counter(r['v'] for r in verdicts))}")
    print(f"ESTIMATED USABLE DECOY PAGES (both prices server-rendered): {total}")
    usable = [h for h in sorted(pools) if any(r["v"] == "BOTH" for r in verdicts if r["host"] == h)]
    print(f"usable hosts: {len(usable)} -> {', '.join(usable)}")
main()
