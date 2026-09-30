import json, sys
from lightpanda import Browser
url = sys.argv[2]
with Browser(binary=sys.argv[1]) as b, b.new_session() as p:
    sd = p.structured_data(url=url, timeout=30000)
    for i, raw in enumerate(sd.get("jsonLd", [])):
        print(f"--- block {i} ({len(raw)} bytes) ---")
        try:
            print(json.dumps(json.loads(raw))[:1400])
        except Exception as e:
            print("unparsable:", e, raw[:300])
    print("--- og keys ---", [k for k, _ in (sd.get("openGraph") or {}).items()] if isinstance(sd.get("openGraph"), dict) else sd.get("openGraph"))
