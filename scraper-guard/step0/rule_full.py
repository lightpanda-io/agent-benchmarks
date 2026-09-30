#!/usr/bin/env python3
"""The free rule, on the FULL page. Pages are 17-51 KB; the model saw 2.4 KB."""
import json, re, sys

pages = {r["url"]: r for r in (json.loads(l) for l in open(sys.argv[2]))}
judged = [r for r in (json.loads(l) for l in open(sys.argv[1])) if r["shape"] == "s1"]

def forms(p):
    p = float(p)
    f = {f"{p:.2f}", f"{p:g}", f"{p:.2f}".replace(".", ","), f"{p:g}".replace(".", ",")}
    if p == int(p):
        f |= {str(int(p)), f"{int(p)},00", f"{int(p)}.00"}
    return f

def present(price, text):
    return any(re.search(r"(?<![\d.,])" + re.escape(f) + r"(?![\d])", text) for f in forms(price))

for label, field, cap in (("2.4 KB (what the model saw)", "text", None),
                          ("full page", "full_text", None)):
    per, tp = {}, {"clean_hit": 0, "clean_n": 0, "wrong_hit": 0, "wrong_n": 0}
    for r in judged:
        page = pages.get(r["url"])
        if not page: continue
        text = page[field]
        hit = present(r["price"], text)
        per.setdefault(r["kind"], [0, 0])
        per[r["kind"]][0] += 1; per[r["kind"]][1] += int(hit)
        if r["kind"] == "clean":
            tp["clean_n"] += 1; tp["clean_hit"] += int(hit)
        else:
            tp["wrong_n"] += 1; tp["wrong_hit"] += int(hit)
    tpr = tp["clean_hit"] / tp["clean_n"]
    tnr = 1 - tp["wrong_hit"] / tp["wrong_n"]
    print(f"\n=== rule on {label} ===")
    for kind in ("clean", "was_price", "instalment", "shipping", "sibling"):
        if kind not in per: continue
        n, h = per[kind]
        print(f"  {kind:11s} n={n:3d}  says present: {h:3d} ({h/n:.0%})")
    print(f"  keeps {tpr:.0%} of clean, catches {tnr:.0%} of wrong "
          f"-> balanced acc {(tpr+tnr)/2:.3f}")
