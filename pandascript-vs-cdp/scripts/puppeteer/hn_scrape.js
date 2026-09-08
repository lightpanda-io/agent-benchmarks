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

await page.goto("https://news.ycombinator.com");
step("goto:front");

const stories = await page.$$eval("tr.athing", (rows) =>
  rows.slice(0, 5).map((row) => ({
    id: row.id,
    rank: row.querySelector(".rank")?.textContent ?? "",
    title: row.querySelector(".titleline > a")?.textContent ?? "",
    url: row.querySelector(".titleline > a")?.href ?? "",
  })),
);
step("eval:stories");

const results = [];
for (const story of stories) {
  await page.goto(`https://news.ycombinator.com/item?id=${story.id}`);
  step("goto:item");
  const comments = await page.$$eval("tr.comtr", (rows) =>
    rows.slice(0, 3).map((row) => ({
      user: row.querySelector(".hnuser")?.textContent ?? "",
      text: row.querySelector(".commtext")?.textContent ?? "",
    })),
  );
  step("eval:comments");
  results.push({ rank: story.rank, title: story.title, url: story.url, comments });
}

console.log(JSON.stringify(results));

await page.close();
await context.close();
await browser.disconnect();
step("close");
console.error("BENCH_STEPS " + JSON.stringify(steps));
