const page = new Page();
await page.goto("https://apnews.com/hub/world-news");

const { links } = page.extract({
  links: [{ selector: "a[href*='/article/']", attr: "href", limit: 10 }]
});
const urls = [...new Set(links)].slice(0, 3);

const articles = [];
for (const url of urls) {
  await page.goto(url);
  page.waitForSelector(".RichTextStoryBody p");
  const article = page.extract({
    headline: "h1",
    paragraphs: [{ selector: ".RichTextStoryBody p", limit: 3 }]
  });
  articles.push({ url, headline: article.headline, paragraphs: article.paragraphs });
}

return articles;
