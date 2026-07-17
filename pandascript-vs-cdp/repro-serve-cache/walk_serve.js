// Walk the 6-page fixture site over CDP, waitUntil load; print elapsed ms.
import puppeteer from "puppeteer-core";

const ws = process.env.BROWSER_WS ?? "ws://127.0.0.1:9222";
const browser = await puppeteer.connect({ browserWSEndpoint: ws });
const context = await browser.createBrowserContext();
const page = await context.newPage();

const t0 = Date.now();
for (let n = 1; n <= 6; n++) {
  await page.goto(`http://127.0.0.1:9300/site/${n}?assets=3&ms=30`, {
    waitUntil: "load",
  });
}
console.log(`walk_ms=${Date.now() - t0}`);

await page.close();
await context.close();
await browser.disconnect();
