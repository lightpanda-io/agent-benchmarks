#!/usr/bin/env python3
"""Does a Shopify sale page actually render BOTH prices as text, and does its
JSON-LD agree with products.json? If yes the corpus design is sound:
site-labelled decoy, two cross-checking oracles, presence-rule blind."""
import httpx, json, re, sys
from lightpanda import Browser, ToolError

HOSTS = ["allbirds.com", "outdoorvoices.com", "ridge.com", "brooklinen.com",
         "thursdayboots.com", "peakdesign.com"]
C = httpx.Client(timeout=20, follow_redirects=True,
                 headers={"User-Agent": "Mozilla/5.0 (compatible; lightpanda-research)"})
PRODUCTISH = {"Product", "ProductGroup", "ProductModel"}

def walk(n):
    if isinstance(n, dict):
        yield n
        for v in n.values(): yield from walk(v)
    elif isinstance(n, list):
        for v in n: yield from walk(v)

def typeof(n):
    t = n.get("@type")
    return t if isinstance(t, str) else (t[0] if isinstance(t, list) and t else "")

def ld_price(sd):
    for raw in sd.get("jsonLd", []):
        try: doc = json.loads(raw)
        except Exception: continue
        for n in walk(doc):
            if typeof(n) in PRODUCTISH:
                for m in walk(n):
                    if m.get("price") is not None:
                        try: return float(str(m["price"]).replace(",", "."))
                        except ValueError: pass
    return None

def on_page(v, text):
    v = float(v)
    forms = {f"{v:.2f}", f"{v:g}", f"{v:.2f}".replace(".", ","), f"{v:g}".replace(".", ",")}
    if v == int(v): forms |= {str(int(v)), f"{int(v)},00"}
    return any(re.search(r"(?<![\d.,])" + re.escape(f) + r"(?![\d])", text) for f in forms)

picks = []
for h in HOSTS:
    try:
        ps = C.get(f"https://{h}/products.json", params={"limit": 250}).json()["products"]
    except Exception as e:
        print(f"{h}: products.json {type(e).__name__}"); continue
    for p in ps:
        v = (p.get("variants") or [{}])[0]
        if v.get("compare_at_price") and float(v["compare_at_price"]) > float(v["price"]):
            picks.append({"host": h, "url": f"https://{h}/products/{p['handle']}",
                          "live": float(v["price"]), "was": float(v["compare_at_price"]),
                          "title": p.get("title")})
            break

print(f"{len(picks)} sale pages to verify\n")
ok = 0
with Browser(binary=sys.argv[1]) as b, b.new_session() as page:
    for r in picks:
        try:
            sd = page.structured_data(url=r["url"], timeout=35000)
            md = page.markdown(max_bytes=400000, timeout=35000)
            md = md if isinstance(md, str) else json.dumps(md)
        except ToolError as e:
            print(f"{r['host']:20s} ERR {str(e)[:50]}"); continue
        text = " ".join(md.split())
        ld = ld_price(sd)
        live_on = on_page(r["live"], text)
        was_on = on_page(r["was"], text)
        agree = ld is not None and abs(ld - r["live"]) < 0.01
        good = live_on and was_on and agree
        ok += good
        print(f"{r['host']:20s} {len(text):6d}b live={r['live']:>8} was={r['was']:>8} "
              f"ld={ld} | live_on_page={live_on} was_on_page={was_on} ld_agrees={agree} "
              f"{'USABLE' if good else '--'}")
print(f"\n{ok}/{len(picks)} pages fully usable "
      f"(both prices rendered as text + JSON-LD agrees with products.json)")
