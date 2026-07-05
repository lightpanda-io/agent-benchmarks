import { chromium } from "playwright-core";

const endpoint = process.env.BROWSER_WS ?? "ws://127.0.0.1:9222";
const user = process.env.LP_HN_USERNAME;
const pass = process.env.LP_HN_PASSWORD;
if (!user || !pass) throw new Error("LP_HN_USERNAME / LP_HN_PASSWORD not set");

const browser = await chromium.connectOverCDP(endpoint);
const context = await browser.newContext();
const page = await context.newPage();

await page.goto("https://news.ycombinator.com/login");

await page.fill("input[name=acct]", user);
await page.fill("input[name=pw]", pass);
await Promise.all([
  page.waitForNavigation(),
  page.press("input[name=pw]", "Enter"),
]);

const body = await page.textContent("body");
if (body.includes("Validation required")) throw new Error("captcha: validation required");
if (body.includes("Bad login")) throw new Error("bad login");
await page.waitForSelector("#logout");

await page.goto(`https://news.ycombinator.com/user?id=${user}`);
const karma = await page.textContent(
  "#hnmain table table tr:nth-child(3) td:nth-child(2)",
);

console.log(JSON.stringify({ karma: parseInt(karma, 10) }));

await page.close();
await context.close();
await browser.close();
