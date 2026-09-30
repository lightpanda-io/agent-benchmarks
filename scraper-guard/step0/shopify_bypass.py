#!/usr/bin/env python3
"""The listing grid is JS-rendered on most Shopify stores, so link-scraping the
collection fails. But /products.json and the product sitemap are static. If
those work, the listing is not a blocker at all."""
import httpx, json

HOSTS = ["allbirds.com", "www.gymshark.com", "bombas.com", "brooklinen.com", "rothys.com",
         "vessi.com", "cariuma.com", "thursdayboots.com", "tentree.com", "kotn.com",
         "outdoorvoices.com", "mejuri.com", "away.com", "huckberry.com", "ridge.com",
         "meundies.com", "nakedwines.com", "drsquatch.com", "hydrojug.com", "peakdesign.com"]
C = httpx.Client(timeout=20, follow_redirects=True,
                 headers={"User-Agent": "Mozilla/5.0 (compatible; lightpanda-research)"})
total = 0
for h in HOSTS:
    n_json = on_sale = None
    try:
        r = C.get(f"https://{h}/products.json", params={"limit": 250})
        if r.status_code == 200 and "products" in r.text[:200]:
            ps = r.json().get("products", [])
            n_json = len(ps)
            # a variant with compare_at_price > price IS an on-page decoy by construction
            on_sale = sum(1 for p in ps for v in p.get("variants", [])[:1]
                          if v.get("compare_at_price") and
                          float(v["compare_at_price"]) > float(v["price"]))
        else:
            n_json = f"http {r.status_code}"
    except Exception as e:
        n_json = f"err {type(e).__name__}"
    if isinstance(on_sale, int):
        total += on_sale
    print(f"{h:22s} products.json={str(n_json):>10}  compare_at>price={on_sale}")
print(f"\nsale variants reachable without rendering any listing: {total}")
