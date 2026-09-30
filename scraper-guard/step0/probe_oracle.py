#!/usr/bin/env python3
"""Step 0A: does a JSON-LD Product oracle exist on real detail pages, and is
there a strikethrough price to mutate onto?"""
import json, re, sys
from lightpanda import Browser, ToolError

LISTINGS = [
    ("webscraper.io", "https://webscraper.io/test-sites/e-commerce/allinone", r"/product/"),
    ("books.toscrape.com", "http://books.toscrape.com/catalogue/category/books_1/index.html", r"/catalogue/.+/index\.html"),
    ("allbirds.com", "https://www.allbirds.com/collections/mens", r"/products/"),
    ("patagonia.com", "https://www.patagonia.com/shop/mens-jackets-vests", r"/product/"),
    ("decathlon.es", "https://www.decathlon.es/es/deportes/running", r"/p/"),
    ("uniqlo.com", "https://www.uniqlo.com/eu/en/men/tops", r"/products/"),
]
WAS_PRICE = re.compile(r"<del|class=\"[^\"]*(was|compare|strike|original|old)[^\"]*price|price[^\"]*(was|compare|strike)", re.I)

def products(page, url, pat):
    try:
        res = page.links(url=url, timeout=25000)
    except ToolError as e:
        return None, f"links failed: {e}"
    items = res if isinstance(res, list) else res.get("links", [])
    hrefs = [l["href"] for l in items if re.search(pat, l.get("href", ""))]
    return hrefs, None

def oracle(page, url):
    try:
        sd = page.structured_data(url=url, timeout=25000)
    except ToolError as e:
        return {"err": f"structuredData: {e}"}
    blocks = sd.get("jsonLd", []) if isinstance(sd, dict) else []
    found = {}
    for raw in blocks:
        try:
            doc = json.loads(raw)
        except Exception:
            continue
        for node in (doc if isinstance(doc, list) else [doc]) + (doc.get("@graph", []) if isinstance(doc, dict) else []):
            if not isinstance(node, dict):
                continue
            t = node.get("@type")
            t = t if isinstance(t, str) else (t[0] if isinstance(t, list) and t else "")
            if t == "Product":
                offers = node.get("offers") or {}
                offers = offers[0] if isinstance(offers, list) and offers else offers
                found = {
                    "name": node.get("name"),
                    "price": (offers or {}).get("price"),
                    "currency": (offers or {}).get("priceCurrency"),
                    "sku": node.get("sku"),
                }
    return {"jsonld_blocks": len(blocks), "product": found}

def main():
    binary = sys.argv[1]
    out = []
    with Browser(binary=binary) as browser, browser.new_session() as page:
        for host, listing, pat in LISTINGS:
            hrefs, err = products(page, listing, pat)
            if err or not hrefs:
                out.append({"host": host, "err": err or "no product links matched"})
                print(json.dumps(out[-1])); continue
            detail = hrefs[0]
            o = oracle(page, detail)
            try:
                raw = page.html(max_bytes=400000, timeout=25000)
                html = raw if isinstance(raw, str) else json.dumps(raw)
            except ToolError:
                html = ""
            row = {"host": host, "detail": detail, "n_products": len(hrefs),
                   "was_price_markup": bool(WAS_PRICE.search(html)), **o}
            out.append(row); print(json.dumps(row))
    print("\n--- summary ---")
    for r in out:
        p = (r.get("product") or {})
        ok = "OK " if p.get("price") else "no "
        print(f"{ok} {r['host']:22s} jsonld={r.get('jsonld_blocks','-'):>3} "
              f"price={str(p.get('price')):>10} cur={p.get('currency')} "
              f"was_markup={r.get('was_price_markup')} {r.get('err','')}")

main()
