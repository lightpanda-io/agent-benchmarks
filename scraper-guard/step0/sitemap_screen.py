#!/usr/bin/env python3
"""Sitemap-driven compat screen. Product URLs come from robots.txt -> sitemaps
(static XML, no listing rendering anywhere). A page counts only if the JSON-LD/OG
oracle price AND a second on-page money value are both in the markdown text --
the state the model actually receives.
"""
import gzip, io, json, os, random, re, sys, time
import httpx

HOSTS = """mediamarkt.es mediamarkt.de saturn.de currys.co.uk argos.co.uk johnlewis.com
ao.com boulanger.com darty.com fnac.com elcorteingles.es worten.es pccomponentes.com
coolblue.nl bcc.nl mediaexpert.pl alza.cz elgiganten.se power.dk verkkokauppa.com
newegg.com bhphotovideo.com adorama.com microcenter.com waterstones.com blackwells.co.uk
wordery.com thriftbooks.com betterworldbooks.com bol.com casadellibro.com
leroymerlin.es bauhaus.de screwfix.com toolstation.com boots.com superdrug.com
decathlon.es decathlon.co.uk sportsdirect.com allegro.pl emag.ro""".split()

C = httpx.Client(timeout=25, follow_redirects=True,
                 headers={"User-Agent": "Mozilla/5.0 (compatible; lightpanda-research)"})
CACHE = "/tmp/claude-jev-brief/sitemap-cache"
os.makedirs(CACHE, exist_ok=True)
PRODUCT_HINT = re.compile(r"produ|artikel|item|detail|/p[-_/]|ficha|book|offer", re.I)
LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
SAMPLE = 3
MIN_MD = 800
# $1,234.56 | 1.234,56 € | £99.99 | 99,99 EUR
MONEY = re.compile(r"(?:[$€£]\s?|\b)(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?|\d{1,6}(?:[.,]\d{2})?)\s?(?:[$€£]|EUR|USD|GBP)?",
                   re.I)
MONEY_STRICT = re.compile(r"[$€£]\s?(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)|(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)\s?(?:[$€£]|EUR\b|USD\b|GBP\b)")
PRODUCTISH = {"Product", "ProductGroup", "ProductModel", "IndividualProduct"}

def fetch(url):
    key = os.path.join(CACHE, re.sub(r"\W+", "_", url)[-150:])
    if os.path.exists(key):
        return open(key, "rb").read()
    try:
        r = C.get(url)
        body = r.content if r.status_code == 200 else b""
    except Exception:
        body = b""
    if url.endswith(".gz") or body[:2] == b"\x1f\x8b":
        try: body = gzip.decompress(body)
        except Exception: pass
    open(key, "wb").write(body)
    return body

def sitemaps_for(host):
    out = []
    rb = fetch(f"https://{host}/robots.txt").decode("utf-8", "ignore")
    out += re.findall(r"(?im)^\s*sitemap:\s*(\S+)", rb)
    if not out:
        out = [f"https://{host}/sitemap.xml"]
    return out[:6]

def product_urls(host):
    seen, urls = set(), []
    queue = sitemaps_for(host)
    opened = 0
    while queue and opened < 3 and len(urls) < 400:
        sm = queue.pop(0)
        body = fetch(sm)
        if not body: continue
        text = body.decode("utf-8", "ignore")
        locs = LOC.findall(text)
        if not locs: continue
        if "<sitemapindex" in text[:3000].lower():
            kids = [l for l in locs if PRODUCT_HINT.search(l)] or locs
            queue = kids[:3] + queue
            continue
        opened += 1
        for l in locs:
            if PRODUCT_HINT.search(l) and l not in seen:
                seen.add(l); urls.append(l)
        if not urls:
            for l in locs[:400]:
                if l not in seen and l.rstrip("/") != f"https://{host}":
                    seen.add(l); urls.append(l)
    return urls

def walk(n):
    if isinstance(n, dict):
        yield n
        for v in n.values(): yield from walk(v)
    elif isinstance(n, list):
        for v in n: yield from walk(v)

def typeof(n):
    t = n.get("@type")
    return t if isinstance(t, str) else (t[0] if isinstance(t, list) and t else "")

def num(s):
    s = s.strip()
    if re.search(r",\d{2}$", s): s = s.replace(".", "").replace(",", ".")
    else: s = s.replace(",", "")
    try: return round(float(s), 2)
    except ValueError: return None

def oracle(sd):
    for raw in sd.get("jsonLd", []):
        try: doc = json.loads(raw)
        except Exception: continue
        for n in walk(doc):
            if typeof(n) not in PRODUCTISH: continue
            for m in walk(n):
                if m.get("price") is not None:
                    v = num(str(m["price"]))
                    if v: return {"src": "ld", "price": v, "name": n.get("name"),
                                  "currency": m.get("priceCurrency")}
    og = sd.get("openGraph") or {}
    if isinstance(og, dict) and og.get("price:amount"):
        v = num(str(og["price:amount"]))
        if v: return {"src": "og", "price": v, "name": None, "currency": og.get("price:currency")}
    return None

def money_in(text):
    vals = set()
    for m in MONEY_STRICT.finditer(text):
        v = num(m.group(1) or m.group(2) or "")
        if v and 0.5 <= v <= 20000: vals.add(v)
    return vals

def present(v, text):
    forms = {f"{v:.2f}", f"{v:g}", f"{v:.2f}".replace(".", ","), f"{v:g}".replace(".", ",")}
    if v == int(v): forms |= {str(int(v)), f"{int(v)},00", f"{int(v)}.00"}
    return any(re.search(r"(?<![\d.,])" + re.escape(f) + r"(?![\d])", text) for f in forms)

def main():
    from lightpanda import Browser, ToolError
    rows, pools = [], {}
    random.seed(7)
    plan = []
    for h in HOSTS:
        us = product_urls(h)
        pools[h] = len(us)
        if us:
            plan.append((h, random.sample(us, min(SAMPLE, len(us)))))
        print(f"{h:22s} sitemap urls: {len(us)}", flush=True)
    print(f"\n-- browser: {sum(len(u) for _, u in plan)} pages across {len(plan)} hosts --\n", flush=True)
    with Browser(binary=sys.argv[1]) as b, b.new_session() as p:
        for host, urls in plan:
            for u in urls:
                try:
                    sd = p.structured_data(url=u, timeout=30000)
                    md = p.markdown(max_bytes=400000, timeout=30000)
                    md = md if isinstance(md, str) else json.dumps(md)
                except ToolError as e:
                    rows.append({"host": host, "url": u, "v": "err"})
                    print(f"  {host:20s} ERR {str(e)[:42]}", flush=True); continue
                text = " ".join(md.split())
                o = oracle(sd)
                vals = money_in(text)
                if len(text) < MIN_MD: v = "blank"
                elif not o: v = "no_oracle"
                elif not present(o["price"], text): v = "price_not_in_text"
                else:
                    decoys = sorted(x for x in vals
                                    if abs(x - o["price"]) > 0.01 and o["price"] * 0.1 < x < o["price"] * 5)
                    v = "USABLE" if decoys else "no_decoy"
                rows.append({"host": host, "url": u, "v": v, "md": len(text),
                             "oracle": o, "n_money": len(vals)})
                print(f"  {host:20s} {v:18s} md={len(text):6d}b "
                      f"oracle={(o or {}).get('price')} money={len(vals)}", flush=True)
                time.sleep(0.8)
    with open(sys.argv[2], "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")

    print("\n" + "=" * 78)
    print(f"{'host':22s} {'pool':>6s} {'USABLE':>7s}/{'seen':<4s} {'est':>6s}")
    total, usable = 0, []
    for host in HOSTS:
        vs = [r for r in rows if r["host"] == host and r["v"] != "err"]
        if not vs: continue
        good = sum(1 for r in vs if r["v"] == "USABLE")
        est = round(pools[host] * good / len(vs))
        total += est
        if good: usable.append((host, est))
        print(f"{host:22s} {pools[host]:6d} {good:7d}/{len(vs):<4d} {est:6d}"
              f"{'  <= USABLE' if good else ''}")
    from collections import Counter
    print(f"\nverdicts: {dict(Counter(r['v'] for r in rows))}")
    print(f"\nESTIMATED CORPUS: {total} pages across {len(usable)} hosts")
    for h, e in sorted(usable, key=lambda x: -x[1])[:14]:
        print(f"   {h:22s} ~{e:6d}  {e/total:5.0%}" if total else "")
main()
