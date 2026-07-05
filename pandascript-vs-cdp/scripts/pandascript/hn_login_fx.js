const page = new Page();
await page.goto("$LP_BASE_URL/login");

page.fill("input[name=acct]", "$LP_HN_USERNAME");
page.fill("input[name=pw]", "$LP_HN_PASSWORD");
page.press("input[name=pw]", "Enter");

page.waitForState({ state: "load" });
page.waitForSelector("#logout");

await page.goto("$LP_BASE_URL/user?id=$LP_HN_USERNAME");
const { karma } = page.extract({
  karma: "#hnmain table table tr:nth-child(3) td:nth-child(2)"
});

return { karma: parseInt(karma, 10) };
