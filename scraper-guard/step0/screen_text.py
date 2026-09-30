#!/usr/bin/env python3
"""Screen on the criterion that matches the model's state: are BOTH prices in
the markdown we would actually send? Raw-HTML presence is not enough -- Shopify
embeds product JSON in <script>, which no text read exposes."""
import httpx, json, re, sys, time
from lightpanda import Browser, ToolError

HOSTS = ["allbirds.com", "brooklinen.com", "chubbiesshorts.com", "drsquatch.com",
         "mackweldon.com", "outdoorvoices.com", "peakdesign.com", "ridge.com",
         "rothys.com", "tentree.com", "thursdayboots.com", "untuckit.com"]
C = httpx.Client(timeout=20, follow_redirects=True,
                 headers={"User-Agent": "Mozilla/5.0 (compatible; lightpanda-research)"})
PER_HOST = 3

def forms(v):
    v = float(v)
    f = {f"{v:.2f}", f"{v:g}"}
    if v == int(v): f |= {str(int(v)), f"{int(v)}.00"}
    return f

def hit(v, text):
    return any(re.search(r"(?<![\d.])" + re.escape(x) + r"(?![\d])", text) for x in forms(v))

def sale_items(host):
    try:
        r = C.get(f"https://{host}/products.json", params={"limit": 250})
        ps = r.json().get("products", []) if r.status_code == 200 else []
    except Exception:
        return [], 0
    out = []
    for p in ps:
        v = (p.get("variants") or [{}])[0]
        try:
            live, was = float(v["price"]), float(v["compare_at_price"])
        except (KeyError, TypeError, ValueError):
            continue
        if was > live:
            out.append({"url": f"https://{host}/products/{p['handle']}", "live": live, "was": was})
    return out, len(out)

rows, pools = [], {}
with Browser(binary=sys.argv[1]) as b, b.new_session() as p:
    for host in HOSTS:
        items, pool = sale_items(host)
        pools[host] = pool
        for it in items[:PER_HOST]:
            try:
                md = p.markdown(url=it["url"], max_bytes=400000, timeout=30000)
                md = md if isinstance(md, str) else json.dumps(md)
            except ToolError as e:
                rows.append({"host": host, "v": "err", **it})
                print(f"  {host:20s} ERR {str(e)[:40]}", flush=True); continue
            text = " ".join(md.split())
            lv, wv = hit(it["live"], text), hit(it["was"], text)
            v = "BOTH" if (lv and wv) else ("live_only" if lv else ("was_only" if wv else "neither"))
            rows.append({"host": host, "v": v, "md_bytes": len(text), **it})
            print(f"  {host:20s} {v:10s} md={len(text):6d}b live={it['live']} was={it['was']}", flush=True)
            time.sleep(0.8)

with open(sys.argv[2], "w") as f:
    for r in rows: f.write(json.dumps(r) + "\n")

print("\n" + "=" * 72)
print(f"{'host':20s} {'pool':>5s} {'BOTH':>5s}/{'seen':<4s} {'est':>5s}")
total = 0
usable = []
for host in HOSTS:
    vs = [r for r in rows if r["host"] == host]
    if not vs: continue
    both = sum(1 for r in vs if r["v"] == "BOTH")
    est = round(pools[host] * both / len(vs))
    total += est
    if both: usable.append(host)
    print(f"{host:20s} {pools[host]:5d} {both:5d}/{len(vs):<4d} {est:5d}{'  <= USABLE' if both else ''}")
from collections import Counter
print(f"\nverdicts: {dict(Counter(r['v'] for r in rows))}")
print(f"ESTIMATED CORPUS (both prices in markdown): {total} pages across {len(usable)} hosts")
print(f"usable: {', '.join(usable)}")
if total:
    print(f"largest host share: {max(round(pools[h]*sum(1 for r in rows if r['host']==h and r['v']=='BOTH')/max(1,len([r for r in rows if r['host']==h]))) for h in usable)/total:.0%}")
