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

await page.goto("https://apnews.com/hub/world-news");
step("goto:hub");

const links = await page.$$eval("a[href*='/article/']", (anchors) =>
  anchors.slice(0, 10).map((a) => a.href),
);
const urls = [...new Set(links)].slice(0, 3);
step("eval:links");

const articles = [];
for (const url of urls) {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  step("goto:article");
  await page.waitForSelector(".RichTextStoryBody p");
  step("wait:body");
  const headline = await page.$eval("h1", (h) => h.textContent.trim());
  const paragraphs = await page.$$eval(".RichTextStoryBody p", (ps) =>
    ps.slice(0, 3).map((p) => p.textContent.trim()),
  );
  step("eval:article");
  articles.push({ url, headline, paragraphs });
}

console.log(JSON.stringify(articles));

await page.close();
await context.close();
await browser.disconnect();
step("close");
console.error("BENCH_STEPS " + JSON.stringify(steps));
