#!/usr/bin/env python3
"""Is the missing JSON-LD a read-too-early problem or a wall?"""
import json, sys
from lightpanda import Browser, ToolError

URLS = [
    ("rei",       "https://www.rei.com/product/227570/rei-co-op-rainier-rain-jacket-mens"),
    ("zalando",   "https://www.zalando.es/nike-sportswear-club-camiseta-basica-blanco-ni122o0cz-A11.html"),
    ("decathlon", "https://www.decathlon.es/es/p/camiseta-running-transpirable-hombre-kiprun-run-500-dry/_/R-p-308242"),
    ("bol",       "https://www.bol.com/nl/nl/p/de-zeven-zussen/9300000148905/"),
    ("allbirds",  "https://www.allbirds.com/products/mens-dasher-nz-anthracite"),
]

def probe(p, name, url, wait):
    try:
        st = p.goto(url=url, timeout=35000, wait_until=wait)
    except ToolError as e:
        return f"{name:10s} wait={wait:13s} goto FAILED {str(e)[:60]}"
    try:
        n_ld = len(p.structured_data(timeout=20000).get("jsonLd", []))
    except ToolError:
        n_ld = -1
    try:
        md = p.markdown(max_bytes=4000, timeout=20000)
        md = md if isinstance(md, str) else json.dumps(md)
    except ToolError:
        md = ""
    try:
        n_script_ld = p.evaluate(
            script="document.querySelectorAll('script[type=\"application/ld+json\"]').length",
            timeout=20000)
    except ToolError as e:
        n_script_ld = f"err:{str(e)[:30]}"
    head = " ".join(md.split())[:70]
    return (f"{name:10s} wait={wait:13s} status={str(st)[:24]:24s} ld={n_ld} "
            f"scripts={n_script_ld} md={len(md):5d}b | {head}")

with Browser(binary=sys.argv[1]) as b, b.new_session() as p:
    for name, url in URLS:
        for wait in ("load", "networkidle"):
            print(probe(p, name, url, wait), flush=True)
