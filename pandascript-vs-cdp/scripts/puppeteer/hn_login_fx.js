import puppeteer from "puppeteer-core";

// Per-step timings for the harness: elapsed since the previous mark, printed
// as a final BENCH_STEPS line on stderr (bench.py parse_steps).
const steps = [];
let mark = performance.now();
const step = (label) => { const now = performance.now(); steps.push([label, Math.round(now - mark)]); mark = now; };

const endpoint = process.env.BROWSER_WS ?? "ws://127.0.0.1:9222";
const base = process.env.BASE_URL ?? "http://127.0.0.1:9280";
const user = process.env.LP_HN_USERNAME;
const pass = process.env.LP_HN_PASSWORD;
if (!user || !pass) throw new Error("LP_HN_USERNAME / LP_HN_PASSWORD not set");

const browser = await puppeteer.connect(
  endpoint.startsWith("ws://")
    ? { browserWSEndpoint: endpoint }
    : { browserURL: endpoint },
);
step("connect");
const context = await browser.createBrowserContext();
const page = await context.newPage();
step("newpage");

await page.goto(`${base}/login`);
step("goto:login");

await page.type("input[name=acct]", user);
await page.type("input[name=pw]", pass);
step("type");
await Promise.all([
  page.waitForNavigation(),
  page.keyboard.press("Enter"),
]);
step("submit:nav");

const body = await page.$eval("body", (b) => b.textContent);
if (body.includes("Validation required")) throw new Error("captcha: validation required");
if (body.includes("Bad login")) throw new Error("bad login");
await page.waitForSelector("#logout");
step("wait:logout");

await page.goto(`${base}/user?id=${user}`);
step("goto:user");
const karma = await page.$eval(
  "#hnmain table table tr:nth-child(3) td:nth-child(2)",
  (td) => td.textContent,
);
step("eval:karma");

console.log(JSON.stringify({ karma: parseInt(karma, 10) }));

await page.close();
await context.close();
await browser.disconnect();
step("close");
console.error("BENCH_STEPS " + JSON.stringify(steps));
