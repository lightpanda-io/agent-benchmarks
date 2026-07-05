import puppeteer from "puppeteer-core";

const endpoint = process.env.BROWSER_WS ?? "ws://127.0.0.1:9222";
const browser = await puppeteer.connect(
  endpoint.startsWith("ws://")
    ? { browserWSEndpoint: endpoint }
    : { browserURL: endpoint },
);
const context = await browser.createBrowserContext();
const page = await context.newPage();

await page.goto("https://news.ycombinator.com");

const stories = await page.$$eval("tr.athing", (rows) =>
  rows.slice(0, 5).map((row) => ({
    id: row.id,
    rank: row.querySelector(".rank")?.textContent ?? "",
    title: row.querySelector(".titleline > a")?.textContent ?? "",
    url: row.querySelector(".titleline > a")?.href ?? "",
  })),
);

const results = [];
for (const story of stories) {
  await page.goto(`https://news.ycombinator.com/item?id=${story.id}`);
  const comments = await page.$$eval("tr.comtr", (rows) =>
    rows.slice(0, 3).map((row) => ({
      user: row.querySelector(".hnuser")?.textContent ?? "",
      text: row.querySelector(".commtext")?.textContent ?? "",
    })),
  );
  results.push({ rank: story.rank, title: story.title, url: story.url, comments });
}

console.log(JSON.stringify(results));

await page.close();
await context.close();
await browser.disconnect();
