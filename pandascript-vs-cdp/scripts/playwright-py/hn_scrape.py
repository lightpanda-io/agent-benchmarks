import json
import os

from playwright.sync_api import sync_playwright

endpoint = os.environ.get("BROWSER_WS", "ws://127.0.0.1:9222")

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(endpoint)
    context = browser.new_context()
    page = context.new_page()

    page.goto("https://news.ycombinator.com")

    stories = page.eval_on_selector_all(
        "tr.athing",
        """(rows) => rows.slice(0, 5).map((row) => ({
          id: row.id,
          rank: row.querySelector(".rank")?.textContent ?? "",
          title: row.querySelector(".titleline > a")?.textContent ?? "",
          url: row.querySelector(".titleline > a")?.href ?? "",
        }))""",
    )

    results = []
    for story in stories:
        page.goto(f"https://news.ycombinator.com/item?id={story['id']}")
        comments = page.eval_on_selector_all(
            "tr.comtr",
            """(rows) => rows.slice(0, 3).map((row) => ({
              user: row.querySelector(".hnuser")?.textContent ?? "",
              text: row.querySelector(".commtext")?.textContent ?? "",
            }))""",
        )
        results.append({"rank": story["rank"], "title": story["title"],
                        "url": story["url"], "comments": comments})

    print(json.dumps(results))

    page.close()
    context.close()
    browser.close()
