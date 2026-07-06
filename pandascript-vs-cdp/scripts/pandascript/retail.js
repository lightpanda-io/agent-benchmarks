const page = new Page();
await page.goto("https://www.allbirds.com/collections/mens-shoes");

const { products } = page.extract({
  products: [{
    selector: "[data-product-card]",
    limit: 3,
    fields: {
      name: { selector: "", attr: "data-product-name" },
      color: { selector: "", attr: "data-product-color" },
      url: { selector: "a[href*='/products/']", attr: "href" }
    }
  }]
});

for (const product of products) {
  await page.goto(product.url);
  page.waitForSelector("[data-testid^=pdp-size-selector-button]");
  const details = page.extract({
    price: { selector: "meta[property='og:price:amount']", attr: "content" },
    sizes: ["[data-testid=pdp-size-selector-button-available]"]
  });
  product.price = parseFloat(details.price);
  product.sizesAvailable = details.sizes;
}

return products;
