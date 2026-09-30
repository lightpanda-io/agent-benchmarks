#!/usr/bin/env python3
"""Step 0B harvest. Token-free: oracle price/name + page text, per product page."""
import json, re, sys, time
from lightpanda import Browser, ToolError

SITES = [
    ("allbirds", "https://www.allbirds.com/collections/mens",
     r"^https://www\.allbirds\.com/products/[a-z0-9-]+$", 10),
    ("mediamarkt", "https://www.mediamarkt.es/es/category/portatiles-478.html",
     r"/es/product/", 10),
]
PRODUCTISH = {"Product", "ProductGroup", "ProductModel", "IndividualProduct"}

def walk(n):
    if isinstance(n, dict):
        yield n
        for v in n.values(): yield from walk(v)
    elif isinstance(n, list):
        for v in n: yield from walk(v)

def typeof(n):
    t = n.get("@type")
    return t if isinstance(t, str) else (t[0] if isinstance(t, list) and t else "")

def oracle(sd):
    for raw in sd.get("jsonLd", []):
        try: doc = json.loads(raw)
        except Exception: continue
        for n in walk(doc):
            if typeof(n) not in PRODUCTISH: continue
            name = n.get("name")
            for m in walk(n):
                if m.get("price") is not None:
                    return {"name": name, "price": m["price"], "currency": m.get("priceCurrency")}
    return None

def main():
    binary, out_path = sys.argv[1], sys.argv[2]
    rows = []
    with Browser(binary=binary) as b, b.new_session() as p:
        for site, listing, pat, want in SITES:
            try:
                res = p.links(url=listing, timeout=35000)
            except ToolError as e:
                print(f"!! {site} listing failed: {str(e)[:70]}"); continue
            items = res if isinstance(res, list) else res.get("links", [])
            urls, seen = [], set()
            for l in items:
                h = l.get("href", "")
                if re.search(pat, h) and h not in seen and h != listing:
                    seen.add(h); urls.append(h)
                if len(urls) >= want: break
            print(f"-- {site}: {len(urls)} product urls")
            for url in urls:
                try:
                    sd = p.structured_data(url=url, timeout=35000)
                    o = oracle(sd)
                    md = p.markdown(max_bytes=2600, timeout=30000)
                    md = md if isinstance(md, str) else json.dumps(md)
                    title = (sd.get("meta") or {}).get("title") if isinstance(sd.get("meta"), dict) else None
                except ToolError as e:
                    print(f"   x {url[:70]} {str(e)[:50]}"); continue
                if not o or o.get("price") is None:
                    print(f"   - {url[:70]} no oracle"); continue
                rows.append({"site": site, "url": url, "oracle": o,
                             "title": title, "text": " ".join(md.split())[:2400]})
                print(f"   + {o['price']} {o.get('currency')} {str(o['name'])[:44]}")
                time.sleep(1.2)
    with open(out_path, "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")
    print(f"\n{len(rows)} rows -> {out_path}")
main()
