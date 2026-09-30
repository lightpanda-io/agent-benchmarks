#!/usr/bin/env python3
"""Is the price missing from the DOM, or only from markdown's clutter-stripped
output? markdown runs readability grabArticle, which prunes link-dense blocks."""
import json, re, sys
from lightpanda import Browser, ToolError

CASES = [
    ("outdoorvoices.com", "https://outdoorvoices.com/products/womens-doing-things-bra-black", 24.97, 58.0),
    ("ridge.com",         "https://ridge.com/products/titanium-money-clip",                   345.0, 590.0),
    ("thursdayboots.com", "https://thursdayboots.com/products/mens-captain-brown",            159.0, 199.0),
]

def hit(v, text):
    v = float(v)
    f = {f"{v:.2f}", f"{v:g}", f"{v:.2f}".replace(".", ","), f"{v:g}".replace(".", ",")}
    if v == int(v): f |= {str(int(v)), f"{int(v)},00"}
    return any(re.search(r"(?<![\d.,])" + re.escape(x) + r"(?![\d])", text) for x in f)

def main():
    with Browser(binary=sys.argv[1]) as b, b.new_session() as p:
        for host, url, live, was in CASES:
            print(f"\n=== {host}  live={live} was={was}")
            try:
                p.goto(url=url, timeout=35000, wait_until="networkidle")
            except ToolError as e:
                print(f"  goto failed: {str(e)[:60]}"); continue
            reads = {}
            for name, fn in (
                ("markdown",  lambda: p.markdown(max_bytes=400000, timeout=30000)),
                ("html",      lambda: p.html(max_bytes=900000, timeout=30000)),
                ("innerText", lambda: p.evaluate(script="document.body.innerText", timeout=30000)),
                ("textContent", lambda: p.evaluate(script="document.body.textContent", timeout=30000)),
            ):
                try:
                    v = fn()
                    reads[name] = v if isinstance(v, str) else json.dumps(v)
                except ToolError as e:
                    reads[name] = ""
                    print(f"  {name:11s} ERR {str(e)[:50]}")
            for name, text in reads.items():
                if not text: continue
                print(f"  {name:11s} {len(text):8d}b  live={hit(live, text)}  was={hit(was, text)}")
            # where does the money live at all?
            ht = reads.get("html", "")
            if ht:
                monies = sorted(set(re.findall(r"[$€£]\s?\d{1,5}(?:[.,]\d{2})?", ht)))[:14]
                print(f"  money strings in html: {monies}")
main()
