import puppeteer from "puppeteer-core";

const endpoint = process.env.BROWSER_WS ?? "ws://127.0.0.1:9222";
const browser = await puppeteer.connect(
  endpoint.startsWith("ws://")
    ? { browserWSEndpoint: endpoint }
    : { browserURL: endpoint },
);
const context = await browser.createBrowserContext();
const page = await context.newPage();

await page.goto("https://eu.gymshark.com/es-ES/collections/all-products/mens");

const products = await page.$$eval("[class*='product-card_card-wrapper']", (cards) =>
  cards.slice(0, 3).map((card) => ({
    name: card.querySelector("[class*='product-card_title'] a")?.textContent.trim(),
    url: card.querySelector("a[href*='/products/']")?.href ?? "",
  })),
);

for (const product of products) {
  await page.goto(product.url);
  await page.waitForSelector("fieldset[class*='add-to-cart_sizes']");
  product.price = parseFloat((await page.$eval(
    "[class*='product-information_price']",
    (el) => el.textContent,
  )).replace(",", "."));
  product.sizesAvailable = await page.$$eval(
    "fieldset[class*='add-to-cart_sizes'] label[class*='size_size']",
    (labels) => labels.map((l) => l.textContent.trim()),
  );
}

console.log(JSON.stringify(products));

await page.close();
await context.close();
await browser.disconnect();
