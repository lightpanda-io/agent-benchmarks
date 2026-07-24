import json
import os
import re

from lightpanda import Browser

args = os.environ.get("BENCH_LPD_ARGS", "").split()

with Browser(args=args) as b:
    page = b.new_session()
    page.goto(url="https://eu.gymshark.com/es-ES/collections/all-products/mens")

    products = page.extract(schema={
        "products": [{
            "selector": "[class*='product-card_card-wrapper']",
            "limit": 3,
            "fields": {
                "name": {"selector": "[class*='product-card_title'] a"},
                "url": {"selector": "a[href*='/products/']", "attr": "href"},
            },
        }],
    })["products"]

    for product in products:
        page.goto(url=product["url"])
        page.wait_for_selector(selector="fieldset[class*='add-to-cart_sizes']")
        details = page.extract(schema={
            "price": {"selector": "[class*='product-information_price']"},
            "sizes": ["fieldset[class*='add-to-cart_sizes'] label[class*='size_size']"],
        })
        product["price"] = float(re.search(r"\d+(?:\.\d+)?", details["price"].replace(",", ".")).group())
        product["sizesAvailable"] = details["sizes"]

print(json.dumps(products))
