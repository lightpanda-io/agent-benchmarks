import json
import re
import time


def goto(url, timeout=30.0):
    """goto_url + commit fence. The harness's polling waits race across
    navigations: the page being left still reports readyState "complete"
    and answers selector queries, so a bare goto_url followed by a poll
    can read the *previous* document. Poll until location.href leaves the
    old page before any readiness wait. (Every navigation in these scripts
    targets a different URL, which is what makes href the commit signal.)"""
    def dismiss_dialog():
        # A pending JS dialog (beforeunload/alert from the old page or an ad)
        # blocks navigation; accepting when none is open just errors, so try.
        try:
            cdp("Page.handleJavaScriptDialog", accept=True)
        except RuntimeError:
            pass

    try:
        prev = js("location.href")
    except RuntimeError:
        prev = None
    dismiss_dialog()
    goto_url(url)
    deadline = time.time() + timeout
    i = 0
    while time.time() < deadline:
        try:
            if js("location.href") != prev:
                return
        except RuntimeError:
            pass
        i += 1
        if i % 20 == 0:
            dismiss_dialog()
        time.sleep(0.05)
    raise RuntimeError(f"navigation to {url} did not commit (tab stuck on {prev})")

goto("https://eu.gymshark.com/es-ES/collections/all-products/mens")
wait_for_load()

products = json.loads(js("""JSON.stringify(Array.from(document.querySelectorAll("[class*='product-card_card-wrapper']")).slice(0, 3).map((card) => ({
  name: card.querySelector("[class*='product-card_title'] a")?.textContent.trim(),
  url: card.querySelector("a[href*='/products/']")?.href ?? "",
})))"""))

# No waitUntil here: after the commit fence, the selector wait is the
# readiness signal — the browser-use idiom for the same dcl+waitForSelector
# pattern the other legs use.
for product in products:
    goto(product["url"])
    wait_for_element("fieldset[class*='add-to-cart_sizes']")
    price_text = js("""document.querySelector("[class*='product-information_price']").textContent""")
    product["price"] = float(re.search(r"\d+(?:\.\d+)?", price_text.replace(",", ".")).group())
    product["sizesAvailable"] = json.loads(js("""JSON.stringify(Array.from(document.querySelectorAll("fieldset[class*='add-to-cart_sizes'] label[class*='size_size']")).map((l) => l.textContent.trim()))"""))

print(json.dumps(products))
