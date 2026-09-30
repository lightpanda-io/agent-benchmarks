#!/usr/bin/env python3
"""Generic deep walk: find any node whose @type is Product-ish and that carries
a price anywhere beneath it. Tests whether the oracle is reachable at all."""
import json, sys
from lightpanda import Browser, ToolError

URLS = [
    ("allbirds",   "https://www.allbirds.com/products/mens-dasher-nz-anthracite"),
    ("rei",        "https://www.rei.com/product/227570/rei-co-op-rainier-rain-jacket-mens"),
    ("mediamarkt", "https://www.mediamarkt.es/es/product/_altavoz-de-estanteria-thomson-ws602duo-100-w-bluetooth-50-2-entradas-rca-mando-a-distancia-ecualizador-marron-1667121.html"),
    ("zalando",    "https://www.zalando.es/nike-sportswear-club-camiseta-basica-blanco-ni122o0cz-A11.html"),
    ("decathlon",  "https://www.decathlon.es/es/p/camiseta-running-transpirable-hombre-kiprun-run-500-dry/_/R-p-308242"),
    ("bol",        "https://www.bol.com/nl/nl/p/de-zeven-zussen/9300000148905/"),
]
PRODUCTISH = {"Product", "ProductGroup", "ProductModel", "IndividualProduct"}

def walk(n, depth=0):
    if isinstance(n, dict):
        yield n
        for v in n.values():
            yield from walk(v, depth + 1)
    elif isinstance(n, list):
        for v in n:
            yield from walk(v, depth + 1)

def typeof(n):
    t = n.get("@type")
    return t if isinstance(t, str) else (t[0] if isinstance(t, list) and t else "")

def price_under(n):
    for m in walk(n):
        for k in ("price", "lowPrice", "highPrice"):
            if m.get(k) is not None:
                return m.get(k), m.get("priceCurrency"), typeof(m) or "?"
    return None, None, None

def main():
    with Browser(binary=sys.argv[1]) as b, b.new_session() as p:
        for name, url in URLS:
            try:
                sd = p.structured_data(url=url, timeout=30000)
            except ToolError as e:
                print(f"-- {name:11s} structuredData failed: {str(e)[:70]}"); continue
            blocks = sd.get("jsonLd", [])
            hit = None
            for raw in blocks:
                try:
                    doc = json.loads(raw)
                except Exception:
                    continue
                for n in walk(doc):
                    if typeof(n) in PRODUCTISH:
                        pr, cur, where = price_under(n)
                        if pr is not None:
                            hit = (typeof(n), n.get("name"), pr, cur, where)
                            break
                if hit:
                    break
            og = sd.get("openGraph") or {}
            ogp = og.get("price:amount") if isinstance(og, dict) else None
            if hit:
                t, nm, pr, cur, where = hit
                print(f"LD {name:11s} blocks={len(blocks)} {t}/{where} price={pr} {cur or ''} "
                      f"name={(nm or '')[:38]!r}")
            else:
                print(f"-- {name:11s} blocks={len(blocks)} no productish+price   og_price={ogp}")
main()
