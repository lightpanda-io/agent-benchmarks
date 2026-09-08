import { chromium } from "playwright-core";

const endpoint = process.env.BROWSER_WS ?? "ws://127.0.0.1:9222";
const browser = await chromium.connectOverCDP(endpoint);
const context = await browser.newContext();
const page = await context.newPage();

await page.goto("https://www.outdoorvoices.com/collections/m-shorts");

const products = await page.$$eval("product-card", (cards) =>
  cards.slice(0, 3).map((card) => ({
    name: card.querySelector("a.product-card__title")?.textContent.trim(),
    url: card.querySelector("a.product-card__title")?.href ?? "",
  })),
);

for (const product of products) {
  await page.goto(product.url, { waitUntil: "domcontentloaded" });
  // The size names sit in a popup that stays hidden until clicked: wait for
  // presence, not visibility (puppeteer's waitForSelector default).
  await page.waitForSelector(".product-form__option-value-name", { state: "attached" });
  product.price = parseFloat((await page.$eval(
    "price-snippet.price .price__item",
    (el) => el.textContent,
  )).replace(/[^0-9.]/g, ""));
  product.sizesAvailable = await page.$$eval(
    ".product-form__option-value-name",
    (labels) => labels.map((l) => l.textContent.trim()),
  );
}

console.log(JSON.stringify(products));

await page.close();
await context.close();
await browser.close();
