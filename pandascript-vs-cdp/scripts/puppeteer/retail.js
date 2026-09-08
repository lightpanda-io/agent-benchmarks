import puppeteer from "puppeteer-core";

// Per-step timings for the harness: elapsed since the previous mark, printed
// as a final BENCH_STEPS line on stderr (bench.py parse_steps). Repeated
// labels are the per-page loop; report.py totals them per run.
const steps = [];
let mark = performance.now();
const step = (label) => { const now = performance.now(); steps.push([label, Math.round(now - mark)]); mark = now; };

const endpoint = process.env.BROWSER_WS ?? "ws://127.0.0.1:9222";
const browser = await puppeteer.connect(
  endpoint.startsWith("ws://")
    ? { browserWSEndpoint: endpoint }
    : { browserURL: endpoint },
);
step("connect");
const context = await browser.createBrowserContext();
const page = await context.newPage();
step("newpage");

await page.goto("https://www.outdoorvoices.com/collections/m-shorts");
step("goto:collection");

const products = await page.$$eval("product-card", (cards) =>
  cards.slice(0, 3).map((card) => ({
    name: card.querySelector("a.product-card__title")?.textContent.trim(),
    url: card.querySelector("a.product-card__title")?.href ?? "",
  })),
);
step("eval:products");

for (const product of products) {
  await page.goto(product.url, { waitUntil: "domcontentloaded" });
  step("goto:product");
  await page.waitForSelector(".product-form__option-value-name");
  step("wait:sizes");
  product.price = parseFloat((await page.$eval(
    "price-snippet.price .price__item",
    (el) => el.textContent,
  )).replace(/[^0-9.]/g, ""));
  product.sizesAvailable = await page.$$eval(
    ".product-form__option-value-name",
    (labels) => labels.map((l) => l.textContent.trim()),
  );
  step("eval:product");
}

console.log(JSON.stringify(products));

await page.close();
await context.close();
await browser.disconnect();
step("close");
console.error("BENCH_STEPS " + JSON.stringify(steps));
