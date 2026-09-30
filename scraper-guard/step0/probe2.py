#!/usr/bin/env python3
"""Step 0A, take 2: Product|ProductGroup|hasVariant|@graph, plus OG price as a
second free oracle to cross-check against."""
import json, re, sys
from lightpanda import Browser, ToolError

LISTINGS = [
    ("allbirds.com",   "https://www.allbirds.com/collections/mens",              r"^https://www\.allbirds\.com/products/[a-z0-9-]+$"),
    ("gymshark.com",   "https://eu.gymshark.com/collections/mens-t-shirts",      r"/products/[a-z0-9-]+"),
    ("rei.com",        "https://www.rei.com/c/mens-jackets",                     r"/product/\d+/"),
    ("uniqlo.com",     "https://www.uniqlo.com/eu/en/men",                       r"/products/E\d+"),
    ("zalando.es",     "https://www.zalando.es/ropa-de-hombre/",                 r"\.html$"),
    ("etsy.com",       "https://www.etsy.com/c/clothing",                        r"/listing/\d+"),
    ("bol.com",        "https://www.bol.com/nl/nl/l/boeken/8299/",               r"/p/[^/]+/\d+"),
    ("mediamarkt.es",  "https://www.mediamarkt.es/es/category/portatiles-478.html", r"/product/"),
]
TYPES = {"Product", "ProductGroup"}

def nodes(doc):
    stack = [doc]
    while stack:
        n = stack.pop()
        if isinstance(n, list):
            stack.extend(n)
        elif isinstance(n, dict):
            yield n
            for k in ("@graph", "hasVariant", "itemListElement", "mainEntity"):
                if k in n:
                    stack.append(n[k])

def oracle(page, url):
    try:
        sd = page.structured_data(url=url, timeout=30000)
    except ToolError as e:
        return {"err": str(e)[:90]}
    out = {"jsonld_blocks": len(sd.get("jsonLd", []))}
    for raw in sd.get("jsonLd", []):
        try:
            doc = json.loads(raw)
        except Exception:
            continue
        for n in nodes(doc):
            t = n.get("@type")
            t = t if isinstance(t, str) else (t[0] if isinstance(t, list) and t else "")
            if t in TYPES and "ld_price" not in out:
                off = n.get("offers") or {}
                off = off[0] if isinstance(off, list) and off else off
                if isinstance(off, dict) and off.get("price") is not None:
                    out.update(ld_type=t, ld_name=n.get("name"),
                               ld_price=off.get("price"), ld_cur=off.get("priceCurrency"))
    og = sd.get("openGraph") or {}
    if isinstance(og, dict):
        out["og_price"] = og.get("price:amount")
        out["og_cur"] = og.get("price:currency")
    return out

def main():
    binary = sys.argv[1]
    rows = []
    with Browser(binary=binary) as browser, browser.new_session() as page:
        for host, listing, pat in LISTINGS:
            try:
                res = page.links(url=listing, timeout=30000)
            except ToolError as e:
                rows.append({"host": host, "err": f"listing: {str(e)[:70]}"}); print(json.dumps(rows[-1])); continue
            items = res if isinstance(res, list) else res.get("links", [])
            hrefs = [l["href"] for l in items
                     if re.search(pat, l.get("href", "")) and l.get("href") != listing]
            if not hrefs:
                rows.append({"host": host, "err": f"no product link ({len(items)} links seen)"}); print(json.dumps(rows[-1])); continue
            row = {"host": host, "n_cand": len(hrefs), "detail": hrefs[0], **oracle(page, hrefs[0])}
            rows.append(row); print(json.dumps(row))
    print("\n--- oracle availability ---")
    for r in rows:
        ok = "LD " if r.get("ld_price") is not None else ("OG " if r.get("og_price") else "-- ")
        print(f"{ok} {r['host']:16s} ld={str(r.get('ld_price')):>9} {str(r.get('ld_cur') or ''):3s} "
              f"og={str(r.get('og_price')):>9} type={r.get('ld_type','')} {r.get('err','')}")
main()
