#!/usr/bin/env python3
"""Find pages carrying BOTH a live price and a real on-page compare-at price.
That is the mutation the presence rule cannot see."""
import json, re, sys, time
from lightpanda import Browser, ToolError

LISTINGS = [
    ("allbirds", "https://www.allbirds.com/collections/sale", r"^https://www\.allbirds\.com/products/[a-z0-9-]+$"),
    ("allbirds2", "https://www.allbirds.com/collections/mens-sale", r"^https://www\.allbirds\.com/products/[a-z0-9-]+$"),
    ("mediamarkt", "https://www.mediamarkt.es/es/category/altavoces-461.html", r"/es/product/"),
]
PRODUCTISH = {"Product", "ProductGroup", "ProductModel"}
MONEY = re.compile(r"[€$]\s?(\d{1,4}(?:[.,]\d{2})?)|(\d{1,4}(?:[.,]\d{2})?)\s?[€$]")

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
            for m in walk(n):
                if m.get("price") is not None:
                    return {"name": n.get("name"), "price": m["price"],
                            "currency": m.get("priceCurrency")}
    return None

def main():
    out = []
    with Browser(binary=sys.argv[1]) as b, b.new_session() as p:
        for site, listing, pat in LISTINGS:
            try:
                res = p.links(url=listing, timeout=35000)
            except ToolError as e:
                print(f"!! {site}: {str(e)[:60]}"); continue
            items = res if isinstance(res, list) else res.get("links", [])
            urls, seen = [], set()
            for l in items:
                h = l.get("href", "")
                if re.search(pat, h) and h not in seen:
                    seen.add(h); urls.append(h)
                if len(urls) >= 14: break
            print(f"-- {site}: {len(urls)} urls")
            for url in urls:
                try:
                    sd = p.structured_data(url=url, timeout=35000)
                    o = oracle(sd)
                    md = p.markdown(url=url, max_bytes=400000, timeout=35000)
                    md = md if isinstance(md, str) else json.dumps(md)
                except ToolError:
                    continue
                if not o or o.get("price") is None:
                    continue
                text = " ".join(md.split())
                live = float(o["price"])
                vals = set()
                for m in MONEY.finditer(text):
                    raw = (m.group(1) or m.group(2)).replace(",", ".")
                    try: vals.add(round(float(raw), 2))
                    except ValueError: pass
                # an on-page money value ABOVE the live price = a plausible was-price
                higher = sorted(v for v in vals if v > live * 1.05 and v < live * 4)
                if higher:
                    out.append({"site": site, "url": url, "oracle": o,
                                "decoy": higher[0], "on_page_money": sorted(vals)[:12],
                                "full_text": text})
                    print(f"   + live={live} decoy={higher[0]} {str(o['name'])[:40]}")
                time.sleep(1.0)
    with open(sys.argv[2], "w") as f:
        for r in out: f.write(json.dumps(r) + "\n")
    print(f"\n{len(out)} rows with an on-page decoy -> {sys.argv[2]}")
main()
