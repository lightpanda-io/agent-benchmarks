#!/usr/bin/env python3
"""Per-class AUC for the model, and the free rule that has to be beaten."""
import json, re, statistics as st, sys

judged = [json.loads(l) for l in open(sys.argv[1])]
pages = {r["url"]: r for r in (json.loads(l) for l in open(sys.argv[2]))}

def auc(pos, neg):
    if not pos or not neg: return float("nan")
    return (sum(1 for a in pos for b in neg if a > b)
            + 0.5 * sum(1 for a in pos for b in neg if a == b)) / (len(pos) * len(neg))

def money_in_text(price, text):
    """Free rule: does the extracted price appear as money in the page text?"""
    p = float(price)
    forms = {f"{p:.2f}", f"{p:g}", f"{p:.2f}".replace(".", ","), f"{p:g}".replace(".", ",")}
    if p == int(p):
        forms |= {str(int(p)), f"{int(p)},00", f"{int(p)}.00"}
    return any(re.search(r"(?<![\d.,])" + re.escape(f) + r"(?![\d])", text) for f in forms)

for shape in ("s0", "s1"):
    rows = [r for r in judged if r["shape"] == shape]
    clean = [r["price_is_current"] for r in rows if r["kind"] == "clean"]
    print(f"\n=== {shape}: per-class AUC(clean > this class), price_is_current ===")
    for kind in ("was_price", "instalment", "shipping", "sibling"):
        neg = [r["price_is_current"] for r in rows if r["kind"] == kind]
        print(f"  {kind:11s} n={len(neg):3d}  AUC={auc(clean, neg):.3f}   median={st.median(neg):.3f}")
    allneg = [r["price_is_current"] for r in rows if r["kind"] != "clean"]
    print(f"  {'ALL':11s} n={len(allneg):3d}  AUC={auc(clean, allneg):.3f}")

    # best single threshold, balanced accuracy
    best = (0, None)
    for t in [i / 100 for i in range(1, 100)]:
        tpr = sum(1 for v in clean if v >= t) / len(clean)
        tnr = sum(1 for v in allneg if v < t) / len(allneg)
        ba = (tpr + tnr) / 2
        if ba > best[0]: best = (ba, t, tpr, tnr)
    print(f"  best threshold {best[1]:.2f}: balanced acc {best[0]:.3f} "
          f"(keeps {best[2]:.0%} of clean, catches {best[3]:.0%} of wrong)")

print("\n=== the free rule: extracted price appears as money in the page text ===")
tp = fp = tn = fn = 0
per = {}
for r in (x for x in judged if x["shape"] == "s1"):
    page = pages.get(r["url"])
    if not page: continue
    hit = money_in_text(r["price"], page["text"])
    ok = r["kind"] == "clean"
    per.setdefault(r["kind"], [0, 0])
    per[r["kind"]][0] += 1
    per[r["kind"]][1] += int(hit)
    if ok and hit: tp += 1
    elif ok and not hit: fn += 1
    elif not ok and hit: fp += 1
    else: tn += 1
for kind, (n, hits) in per.items():
    print(f"  {kind:11s} n={n:3d}  rule says 'present': {hits:3d} ({hits/n:.0%})")
tpr = tp / (tp + fn) if tp + fn else 0
tnr = tn / (tn + fp) if tn + fp else 0
print(f"  balanced acc {(tpr+tnr)/2:.3f}  (keeps {tpr:.0%} of clean, catches {tnr:.0%} of wrong)")
