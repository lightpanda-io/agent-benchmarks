#!/usr/bin/env python3
"""Compat screen: how many pages can we actually reach that carry BOTH a
JSON-LD/OG oracle price AND an on-page decoy money value?

Per host: does the listing render, do product links match, do product pages
render, is the oracle there, is a decoy there. 3 product pages per host is
enough to judge the host and estimate its yield.
"""
import json, re, sys, time
from lightpanda import Browser, ToolError

# Shopify DTC: uniform /collections/<x> + /products/<slug>, and Shopify emits
# Product/ProductGroup JSON-LD by default. Sale collections are where the
# struck-through compare_at_price lives.
SHOPIFY = [
    "allbirds.com", "www.gymshark.com", "bombas.com", "brooklinen.com", "rothys.com",
    "vessi.com", "cariuma.com", "thursdayboots.com", "tentree.com", "kotn.com",
    "outdoorvoices.com", "mejuri.com", "away.com", "huckberry.com", "ridge.com",
    "meundies.com", "nakedwines.com", "drsquatch.com", "hydrojug.com", "peakdesign.com",
]
SHOPIFY_LISTINGS = ["/collections/sale", "/collections/all"]
OTHER = [
    ("mediamarkt.es", "https://www.mediamarkt.es/es/category/altavoces-461.html", r"/es/product/"),
    ("mediamarkt.es2", "https://www.mediamarkt.es/es/category/portatiles-478.html", r"/es/product/"),
    ("fnac.es", "https://www.fnac.es/Informatica/Portatiles/s101914", r"/a\d+/"),
    ("pccomponentes.com", "https://www.pccomponentes.com/portatiles", r"/[a-z0-9-]+$"),
    ("worten.es", "https://www.worten.es/produtos/informatica-portatiles", r"/produtos/"),
    ("coolblue.nl", "https://www.coolblue.nl/laptops", r"/product/\d+"),
    ("waterstones.com", "https://www.waterstones.com/category/fiction", r"/book/"),
    ("blackwells.co.uk", "https://blackwells.co.uk/bookshop/category/fiction", r"/product/"),
    ("thriftbooks.com", "https://www.thriftbooks.com/browse/", r"/w/"),
    ("wordery.com", "https://wordery.com/fiction", r"/[a-z0-9-]+-9\d{12}"),
]
PRODUCTISH = {"Product", "ProductGroup", "ProductModel", "IndividualProduct"}
MONEY = re.compile(r"[€$£]\s?(\d{1,5}(?:[.,]\d{2})?)|(\d{1,5}(?:[.,]\d{2})?)\s?[€$£]")
MIN_RENDER = 800

def walk(n):
    if isinstance(n, dict):
        yield n
        for v in n.values(): yield from walk(v)
    elif isinstance(n, list):
        for v in n: yield from walk(v)

def typeof(n):
    t = n.get("@type")
    return t if isinstance(t, str) else (t[0] if isinstance(t, list) and t else "")

def oracle_of(sd):
    for raw in sd.get("jsonLd", []):
        try: doc = json.loads(raw)
        except Exception: continue
        for n in walk(doc):
            if typeof(n) not in PRODUCTISH: continue
            for m in walk(n):
                if m.get("price") is not None:
                    try: pr = float(str(m["price"]).replace(",", "."))
                    except ValueError: continue
                    return {"src": "ld", "name": n.get("name"), "price": pr,
                            "currency": m.get("priceCurrency")}
    og = sd.get("openGraph") or {}
    if isinstance(og, dict) and og.get("price:amount"):
        try:
            return {"src": "og", "name": (sd.get("meta") or {}).get("title"),
                    "price": float(str(og["price:amount"]).replace(",", ".")),
                    "currency": og.get("price:currency")}
        except ValueError: pass
    return None

def money_values(text):
    vals = set()
    for m in MONEY.finditer(text):
        try: vals.add(round(float((m.group(1) or m.group(2)).replace(",", ".")), 2))
        except ValueError: pass
    return vals

def screen_product(p, url):
    r = {"url": url}
    try:
        sd = p.structured_data(url=url, timeout=30000)
        md = p.markdown(max_bytes=400000, timeout=30000)
        md = md if isinstance(md, str) else json.dumps(md)
    except ToolError as e:
        r["err"] = str(e)[:60]; return r
    text = " ".join(md.split())
    r["bytes"] = len(text)
    if len(text) < MIN_RENDER:
        r["verdict"] = "blank_or_wall"; return r
    o = oracle_of(sd)
    if not o:
        r["verdict"] = "no_oracle"; r["ld_blocks"] = len(sd.get("jsonLd", [])); return r
    r["oracle"] = o
    vals = money_values(text)
    live = o["price"]
    hi = sorted(v for v in vals if live * 1.05 < v < live * 4)
    lo = sorted((v for v in vals if 0 < v < live * 0.9), reverse=True)
    r["decoy_hi"] = hi[0] if hi else None
    r["decoy_lo"] = lo[0] if lo else None
    r["verdict"] = "DECOY" if (hi or lo) else "oracle_no_decoy"
    return r

def host_targets(host):
    out = []
    for path in SHOPIFY_LISTINGS:
        out.append((f"https://{host}{path}", r"/products/[a-z0-9-]+"))
    return out

def main():
    out_path = sys.argv[2]
    rows = []
    with Browser(binary=sys.argv[1]) as b, b.new_session() as p:
        targets = [(h, u, pat) for h in SHOPIFY for u, pat in host_targets(h)] + \
                  [(h, u, pat) for h, u, pat in OTHER]
        done_hosts = set()
        for host, listing, pat in targets:
            if host in done_hosts:
                continue
            try:
                res = p.links(url=listing, timeout=30000)
            except ToolError as e:
                print(f"{host:22s} listing ERR {str(e)[:40]}", flush=True); continue
            items = res if isinstance(res, list) else res.get("links", [])
            urls, seen = [], set()
            for l in items:
                h = l.get("href", "")
                if re.search(pat, h) and h not in seen and h.rstrip("/") != listing.rstrip("/"):
                    seen.add(h); urls.append(h)
            if not urls:
                print(f"{host:22s} {listing.split('/')[-1]:14s} no product links ({len(items)} links)", flush=True)
                continue
            done_hosts.add(host)
            print(f"{host:22s} {listing.split('/')[-1]:14s} {len(urls):4d} product links", flush=True)
            for url in urls[:3]:
                r = screen_product(p, url); r["host"] = host
                rows.append(r)
                print(f"    {r.get('verdict', 'err'):16s} {r.get('bytes', 0):7d}b "
                      f"live={(r.get('oracle') or {}).get('price')} "
                      f"hi={r.get('decoy_hi')} lo={r.get('decoy_lo')} {r.get('err','')}", flush=True)
                time.sleep(1.0)
            rows.append({"host": host, "_pool": len(urls)})
    with open(out_path, "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")

    prods = [r for r in rows if "verdict" in r or "err" in r]
    pools = {r["host"]: r["_pool"] for r in rows if "_pool" in r}
    print("\n" + "=" * 74)
    by = {}
    for r in prods:
        by.setdefault(r["host"], []).append(r.get("verdict", "err"))
    print(f"{'host':22s} {'pool':>5s} {'screened':>8s} {'decoy':>6s}  est. decoy pages")
    total_est = 0
    for host, vs in sorted(by.items()):
        pool = pools.get(host, 0)
        d = sum(1 for v in vs if v == "DECOY")
        est = round(pool * d / len(vs)) if vs else 0
        total_est += est
        print(f"{host:22s} {pool:5d} {len(vs):8d} {d:6d}  {est:5d}   {','.join(sorted(set(vs)))}")
    print(f"\nestimated reachable decoy pages: {total_est}")
    from collections import Counter
    print("screened-page verdicts:", dict(Counter(r.get("verdict", "err") for r in prods)))
main()
