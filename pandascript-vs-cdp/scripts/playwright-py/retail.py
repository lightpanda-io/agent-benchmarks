import json
import os
import re

from playwright.sync_api import sync_playwright

endpoint = os.environ.get("BROWSER_WS", "ws://127.0.0.1:9222")

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(endpoint)
    context = browser.new_context()
    page = context.new_page()

    page.goto("https://eu.gymshark.com/es-ES/collections/all-products/mens")

    products = page.eval_on_selector_all(
        "[class*='product-card_card-wrapper']",
        """(cards) => cards.slice(0, 3).map((card) => ({
          name: card.querySelector("[class*='product-card_title'] a")?.textContent.trim(),
          url: card.querySelector("a[href*='/products/']")?.href ?? "",
        }))""",
    )

    for product in products:
        page.goto(product["url"])
        page.wait_for_selector("fieldset[class*='add-to-cart_sizes']")
        price_text = page.text_content("[class*='product-information_price']")
        product["price"] = float(re.search(r"\d+(?:\.\d+)?", price_text.replace(",", ".")).group())
        product["sizesAvailable"] = page.eval_on_selector_all(
            "fieldset[class*='add-to-cart_sizes'] label[class*='size_size']",
            "(labels) => labels.map((l) => l.textContent.trim())",
        )

    print(json.dumps(products))

    page.close()
    context.close()
    browser.close()
