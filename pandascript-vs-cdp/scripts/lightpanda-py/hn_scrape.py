import json
import os

from lightpanda import Browser, ToolError

args = os.environ.get("BENCH_LPD_ARGS", "").split()

with Browser(args=args) as b:
    page = b.new_session()
    page.goto(url="https://news.ycombinator.com")

    stories = page.extract(schema={
        "stories": [{
            "selector": "tr.athing",
            "limit": 5,
            "fields": {
                "id": {"selector": "", "attr": "id"},
                "rank": ".rank",
                "title": ".titleline > a",
                "url": {"selector": ".titleline > a", "attr": "href"},
            },
        }],
    })["stories"]

    results = []
    for story in stories:
        page.goto(url=f"https://news.ycombinator.com/item?id={story['id']}")
        try:
            comments = page.extract(schema={
                "comments": [{
                    "selector": "tr.comtr",
                    "limit": 3,
                    "fields": {"user": ".hnuser", "text": ".commtext"},
                }],
            })["comments"]
        except ToolError:
            comments = []
        results.append({"rank": story["rank"], "title": story["title"],
                        "url": story["url"], "comments": comments})

print(json.dumps(results))
