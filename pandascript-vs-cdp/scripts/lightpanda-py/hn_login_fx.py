import json
import os
import re
import sys

from lightpanda import Browser

base = os.environ.get("BASE_URL", "http://127.0.0.1:9280")
user = os.environ.get("LP_HN_USERNAME")
password = os.environ.get("LP_HN_PASSWORD")
if not user or not password:
    sys.exit("LP_HN_USERNAME / LP_HN_PASSWORD not set")

args = os.environ.get("BENCH_LPD_ARGS", "").split()

with Browser(args=args) as b:
    page = b.new_session()
    page.goto(url=f"{base}/login")

    page.fill(selector="input[name=acct]", value=user)
    page.fill(selector="input[name=pw]", value=password)
    page.press(selector="input[name=pw]", key="Enter")

    page.wait_for_state(state="load")
    page.wait_for_selector(selector="#logout")

    page.goto(url=f"{base}/user?id={user}")
    karma = page.extract(schema={
        "karma": "#hnmain table table tr:nth-child(3) td:nth-child(2)",
    })["karma"]

print(json.dumps({"karma": int(re.search(r"-?\d+", karma).group())}))
