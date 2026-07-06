import { chromium } from "playwright-core";

const endpoint = process.env.BROWSER_WS ?? "ws://127.0.0.1:9222";
const browser = await chromium.connectOverCDP(endpoint);
const context = await browser.newContext();
const page = await context.newPage();

await page.goto("https://apnews.com/hub/world-news");

const links = await page.$$eval("a[href*='/article/']", (anchors) =>
  anchors.slice(0, 10).map((a) => a.href),
);
const urls = [...new Set(links)].slice(0, 3);

const articles = [];
for (const url of urls) {
  await page.goto(url);
  await page.waitForSelector(".RichTextStoryBody p");
  const headline = await page.$eval("h1", (h) => h.textContent.trim());
  const paragraphs = await page.$$eval(".RichTextStoryBody p", (ps) =>
    ps.slice(0, 3).map((p) => p.textContent.trim()),
  );
  articles.push({ url, headline, paragraphs });
}

console.log(JSON.stringify(articles));

await page.close();
await context.close();
await browser.close();
