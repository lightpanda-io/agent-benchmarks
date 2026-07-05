import puppeteer from "puppeteer-core";

const endpoint = process.env.BROWSER_WS ?? "ws://127.0.0.1:9222";
const user = process.env.LP_HN_USERNAME;
const pass = process.env.LP_HN_PASSWORD;
if (!user || !pass) throw new Error("LP_HN_USERNAME / LP_HN_PASSWORD not set");

const browser = await puppeteer.connect(
  endpoint.startsWith("ws://")
    ? { browserWSEndpoint: endpoint }
    : { browserURL: endpoint },
);
const context = await browser.createBrowserContext();
const page = await context.newPage();

await page.goto("https://news.ycombinator.com/login");

await page.type("input[name=acct]", user);
await page.type("input[name=pw]", pass);
await Promise.all([
  page.waitForNavigation(),
  page.keyboard.press("Enter"),
]);

const body = await page.$eval("body", (b) => b.textContent);
if (body.includes("Validation required")) throw new Error("captcha: validation required");
if (body.includes("Bad login")) throw new Error("bad login");
await page.waitForSelector("#logout");

await page.goto(`https://news.ycombinator.com/user?id=${user}`);
const karma = await page.$eval(
  "#hnmain table table tr:nth-child(3) td:nth-child(2)",
  (td) => td.textContent,
);

console.log(JSON.stringify({ karma: parseInt(karma, 10) }));

await page.close();
await context.close();
await browser.disconnect();
