const mainPage = new Page();
await mainPage.goto("https://news.ycombinator.com");

const { stories } = mainPage.extract({
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

const detailPages = stories.map(() => new Page());
await Promise.all(detailPages.map((p, i) => p.goto(`https://news.ycombinator.com/item?id=${stories[i].id}`)));

const results = stories.map((story, i) => {
  let comments = [];
  try {
    ({ comments } = detailPages[i].extract({
      comments: [{
        selector: "tr.comtr",
        limit: 3,
        fields: { user: ".hnuser", text: ".commtext" }
      }]
    }));
  } catch {}
  return { rank: story.rank, title: story.title, url: story.url, comments };
});

return results;
