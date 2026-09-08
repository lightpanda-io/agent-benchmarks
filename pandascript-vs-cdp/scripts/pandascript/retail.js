const page = new Page();
await page.goto("https://www.outdoorvoices.com/collections/m-shorts");

const { products } = page.extract({
  products: [{
    selector: "product-card",
    limit: 3,
    fields: {
      name: { selector: "a.product-card__title" },
      url: { selector: "a.product-card__title", attr: "href" }
    }
  }]
});

for (const product of products) {
  if (product.url.startsWith("/")) product.url = "https://www.outdoorvoices.com" + product.url;
  await page.goto(product.url, { waitUntil: "domcontentloaded" });
  page.waitForSelector(".product-form__option-value-name");
  const details = page.extract({
    price: { selector: "price-snippet.price .price__item" },
    sizes: [".product-form__option-value-name"]
  });
  product.price = parseFloat(details.price.replace(/[^0-9.]/g, ""));
  product.sizesAvailable = details.sizes;
}

return products;
