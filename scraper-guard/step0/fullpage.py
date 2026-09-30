#!/usr/bin/env python3
"""Re-fetch full page text for the smoke urls, so the free rule is not
handicapped by the model's token budget."""
import json, sys
from lightpanda import Browser, ToolError
rows = [json.loads(l) for l in open(sys.argv[2])]
with Browser(binary=sys.argv[1]) as b, b.new_session() as p:
    for r in rows:
        try:
            md = p.markdown(url=r["url"], max_bytes=400000, timeout=35000)
            md = md if isinstance(md, str) else json.dumps(md)
        except ToolError as e:
            print(f"x {r['url'][:60]} {str(e)[:40]}"); md = r["text"]
        r["full_text"] = " ".join(md.split())
        print(f"{r['site']:11s} {len(r['full_text']):7d}b  oracle={r['oracle']['price']}")
with open(sys.argv[3], "w") as f:
    for r in rows: f.write(json.dumps(r) + "\n")
print(f"\n-> {sys.argv[3]}")
