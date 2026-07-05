const page = new Page();
await page.goto("https://news.ycombinator.com");

const { stories } = page.extract({
  stories: [{
    selector: "tr.athing",
    limit: 5,
    fields: {
      id: { selector: "", attr: "id" },
      rank: ".rank",
      title: ".titleline > a",
      url: { selector: ".titleline > a", attr: "href" }
    }
  }]
});

const results = [];
for (const story of stories) {
  await page.goto(`https://news.ycombinator.com/item?id=${story.id}`);
  let comments = [];
  try {
    ({ comments } = page.extract({
      comments: [{
        selector: "tr.comtr",
        limit: 3,
        fields: { user: ".hnuser", text: ".commtext" }
      }]
    }));
  } catch {}
  results.push({ rank: story.rank, title: story.title, url: story.url, comments });
}

return results;
