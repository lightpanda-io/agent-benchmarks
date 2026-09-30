#!/usr/bin/env python3
"""No products.json needed: the HTML screen already recorded url+live+was.
Re-read those pages with markdown and apply the criterion that matches the
model's state."""
import json, re, sys, time
from lightpanda import Browser, ToolError

POOL = {"allbirds.com": 138, "brooklinen.com": 64, "chubbiesshorts.com": 157,
        "drsquatch.com": 11, "mackweldon.com": 193, "outdoorvoices.com": 206,
        "peakdesign.com": 132, "ridge.com": 111, "rothys.com": 11, "tentree.com": 1,
        "thursdayboots.com": 41, "untuckit.com": 67}

def forms(v):
    v = float(v); f = {f"{v:.2f}", f"{v:g}"}
    if v == int(v): f |= {str(int(v)), f"{int(v)}.00"}
    return f

def hit(v, t):
    return any(re.search(r"(?<![\d.])" + re.escape(x) + r"(?![\d])", t) for x in forms(v))

cands = [json.loads(l) for l in open(sys.argv[1]) if json.loads(l).get("v") != "err"]
rows = []
with Browser(binary=sys.argv[2]) as b, b.new_session() as p:
    for c in cands:
        try:
            md = p.markdown(url=c["url"], max_bytes=400000, timeout=30000)
            md = md if isinstance(md, str) else json.dumps(md)
        except ToolError as e:
            rows.append({**c, "t": "err"}); print(f"  {c['host']:20s} ERR {str(e)[:40]}", flush=True); continue
        text = " ".join(md.split())
        lv, wv = hit(c["live"], text), hit(c["was"], text)
        t = "BOTH" if (lv and wv) else ("live_only" if lv else ("was_only" if wv else "neither"))
        rows.append({**c, "t": t, "md": len(text)})
        print(f"  {c['host']:20s} html={c['v']:10s} text={t:10s} md={len(text):6d}b "
              f"live={c['live']} was={c['was']}", flush=True)
        time.sleep(0.7)

with open(sys.argv[3], "w") as f:
    for r in rows: f.write(json.dumps(r) + "\n")
print("\n" + "=" * 74)
print(f"{'host':20s} {'pool':>5s} {'BOTH':>5s}/{'seen':<4s} {'est':>5s}")
total, usable = 0, []
for host in POOL:
    vs = [r for r in rows if r["host"] == host and r["t"] != "err"]
    if not vs: continue
    both = sum(1 for r in vs if r["t"] == "BOTH")
    est = round(POOL[host] * both / len(vs)); total += est
    if both: usable.append((host, est))
    print(f"{host:20s} {POOL[host]:5d} {both:5d}/{len(vs):<4d} {est:5d}{'  <= USABLE' if both else ''}")
from collections import Counter
print(f"\nhtml-criterion verdicts: {dict(Counter(r['v'] for r in rows))}")
print(f"TEXT-criterion verdicts: {dict(Counter(r['t'] for r in rows))}")
print(f"\nESTIMATED CORPUS (both prices in the markdown the model sees): {total} pages, {len(usable)} hosts")
for h, e in sorted(usable, key=lambda x: -x[1]):
    print(f"   {h:22s} ~{e:4d}  ({e/total:.0%} of corpus)" if total else "")
