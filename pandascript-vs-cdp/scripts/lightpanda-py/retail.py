import json
import os
import re

from lightpanda import Browser

args = os.environ.get("BENCH_LPD_ARGS", "").split()

with Browser(args=args) as b:
    page = b.new_session()
    page.goto(url="https://www.outdoorvoices.com/collections/m-shorts")

    products = page.extract(schema={
        "products": [{
            "selector": "product-card",
            "limit": 3,
            "fields": {
                "name": {"selector": "a.product-card__title"},
                "url": {"selector": "a.product-card__title", "attr": "href"},
            },
        }],
    })["products"]

    for product in products:
        if product["url"].startswith("/"):
            product["url"] = "https://www.outdoorvoices.com" + product["url"]
        page.goto(url=product["url"])
        page.wait_for_selector(selector=".product-form__option-value-name")
        details = page.extract(schema={
            "price": {"selector": "price-snippet.price .price__item"},
            "sizes": [".product-form__option-value-name"],
        })
        product["price"] = float(re.search(r"\d+(?:\.\d+)?", details["price"]).group())
        product["sizesAvailable"] = details["sizes"]

print(json.dumps(products))
