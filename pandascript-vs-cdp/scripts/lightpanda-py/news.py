import json
import os

from lightpanda import Browser

args = os.environ.get("BENCH_LPD_ARGS", "").split()

with Browser(args=args) as b:
    page = b.new_session()
    page.goto(url="https://apnews.com/hub/world-news")

    links = page.extract(schema={
        "links": [{"selector": "a[href*='/article/']", "attr": "href", "limit": 10}],
    })["links"]
    urls = list(dict.fromkeys(links))[:3]

    articles = []
    for url in urls:
        page.goto(url=url)
        page.wait_for_selector(selector=".RichTextStoryBody p")
        article = page.extract(schema={
            "headline": "h1",
            "paragraphs": [{"selector": ".RichTextStoryBody p", "limit": 3}],
        })
        articles.append({"url": url, "headline": article["headline"],
                         "paragraphs": article["paragraphs"]})

print(json.dumps(articles))
