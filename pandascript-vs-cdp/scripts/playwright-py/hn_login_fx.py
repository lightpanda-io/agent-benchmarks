import json
import os
import re
import sys

from playwright.sync_api import sync_playwright

endpoint = os.environ.get("BROWSER_WS", "ws://127.0.0.1:9222")
base = os.environ.get("BASE_URL", "http://127.0.0.1:9280")
user = os.environ.get("LP_HN_USERNAME")
password = os.environ.get("LP_HN_PASSWORD")
if not user or not password:
    sys.exit("LP_HN_USERNAME / LP_HN_PASSWORD not set")

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(endpoint)
    context = browser.new_context()
    page = context.new_page()

    page.goto(f"{base}/login")

    page.fill("input[name=acct]", user)
    page.fill("input[name=pw]", password)
    with page.expect_navigation():
        page.press("input[name=pw]", "Enter")

    body = page.text_content("body")
    if "Validation required" in body:
        sys.exit("captcha: validation required")
    if "Bad login" in body:
        sys.exit("bad login")
    page.wait_for_selector("#logout")

    page.goto(f"{base}/user?id={user}")
    karma = page.text_content("#hnmain table table tr:nth-child(3) td:nth-child(2)")

    print(json.dumps({"karma": int(re.search(r"-?\d+", karma).group())}))

    page.close()
    context.close()
    browser.close()
