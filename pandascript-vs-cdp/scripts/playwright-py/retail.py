import json
import os
import re

from playwright.sync_api import sync_playwright

endpoint = os.environ.get("BROWSER_WS", "ws://127.0.0.1:9222")

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(endpoint)
    context = browser.new_context()
    page = context.new_page()

    page.goto("https://www.outdoorvoices.com/collections/m-shorts")

    products = page.eval_on_selector_all(
        "product-card",
        """(cards) => cards.slice(0, 3).map((card) => ({
          name: card.querySelector("a.product-card__title")?.textContent.trim(),
          url: card.querySelector("a.product-card__title")?.href ?? "",
        }))""",
    )

    for product in products:
        page.goto(product["url"], wait_until="domcontentloaded")
        # hidden popup: wait for presence, not visibility
        page.wait_for_selector(".product-form__option-value-name", state="attached")
        price_text = page.text_content("price-snippet.price .price__item")
        product["price"] = float(re.search(r"\d+(?:\.\d+)?", price_text).group())
        product["sizesAvailable"] = page.eval_on_selector_all(
            ".product-form__option-value-name",
            "(labels) => labels.map((l) => l.textContent.trim())",
        )

    print(json.dumps(products))

    page.close()
    context.close()
    browser.close()
