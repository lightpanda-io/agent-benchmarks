import puppeteer from "puppeteer-core";

const endpoint = process.env.BROWSER_WS ?? "ws://127.0.0.1:9222";
const browser = await puppeteer.connect(
  endpoint.startsWith("ws://")
    ? { browserWSEndpoint: endpoint }
    : { browserURL: endpoint },
);
const context = await browser.createBrowserContext();
const page = await context.newPage();

await page.goto("https://www.allbirds.com/collections/mens-shoes");

const products = await page.$$eval("[data-product-card]", (cards) =>
  cards.slice(0, 3).map((card) => ({
    name: card.getAttribute("data-product-name"),
    color: card.getAttribute("data-product-color"),
    url: card.querySelector("a[href*='/products/']")?.href ?? "",
  })),
);

for (const product of products) {
  await page.goto(product.url);
  await page.waitForSelector("[data-testid^=pdp-size-selector-button]");
  product.price = parseFloat(await page.$eval(
    "meta[property='og:price:amount']",
    (meta) => meta.content,
  ));
  product.sizesAvailable = await page.$$eval(
    "[data-testid=pdp-size-selector-button-available]",
    (buttons) => buttons.map((b) => b.textContent.trim()),
  );
}

console.log(JSON.stringify(products));

await page.close();
await context.close();
await browser.disconnect();
