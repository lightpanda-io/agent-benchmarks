const page = new Page();
const t0 = Date.now();
for (let n = 1; n <= 6; n++) {
  await page.goto(`http://127.0.0.1:9300/site/${n}?assets=3&ms=30`);
}
return `walk_ms=${Date.now() - t0}`;
