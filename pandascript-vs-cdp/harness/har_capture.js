// HAR capture of the retail flow's first two page loads, for engine comparison.
// Usage: BROWSER_WS=<ws://... | http://...> node harness/har_capture.js out.har
import { chromium } from "playwright-core";

const endpoint = process.env.BROWSER_WS ?? "ws://127.0.0.1:9222";
const harPath = process.argv[2] ?? "capture.har";

const browser = await chromium.connectOverCDP(endpoint);
const context = await browser.newContext({
  recordHar: { path: harPath, content: "omit" },
});
const page = await context.newPage();

const marks = {};
const t0 = Date.now();
page.on("domcontentloaded", () => { marks.dcl = marks.dcl ?? Date.now() - t0; });
page.on("load", () => { marks.load = marks.load ?? Date.now() - t0; });

await page.goto("https://www.allbirds.com/collections/mens-shoes");
marks.goto1 = Date.now() - t0;

const url = await page.$eval("[data-product-card] a[href*='/products/']", (a) => a.href);

const t1 = Date.now();
await page.goto(url);
marks.goto2 = Date.now() - t1;
await page.waitForSelector("[data-testid^=pdp-size-selector-button]");
marks.hydrated = Date.now() - t1;

console.log(JSON.stringify(marks));

await context.close(); // flushes the HAR
await browser.close();
