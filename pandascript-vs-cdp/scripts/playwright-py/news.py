import json
import os

from playwright.sync_api import sync_playwright

endpoint = os.environ.get("BROWSER_WS", "ws://127.0.0.1:9222")

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(endpoint)
    context = browser.new_context()
    page = context.new_page()

    page.goto("https://apnews.com/hub/world-news")

    links = page.eval_on_selector_all(
        "a[href*='/article/']",
        "(anchors) => anchors.slice(0, 10).map((a) => a.href)",
    )
    urls = list(dict.fromkeys(links))[:3]

    articles = []
    for url in urls:
        page.goto(url)
        page.wait_for_selector(".RichTextStoryBody p")
        headline = page.eval_on_selector("h1", "(h) => h.textContent.trim()")
        paragraphs = page.eval_on_selector_all(
            ".RichTextStoryBody p",
            "(ps) => ps.slice(0, 3).map((p) => p.textContent.trim())",
        )
        articles.append({"url": url, "headline": headline, "paragraphs": paragraphs})

    print(json.dumps(articles))

    page.close()
    context.close()
    browser.close()
