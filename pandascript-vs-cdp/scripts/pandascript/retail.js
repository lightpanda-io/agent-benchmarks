const page = new Page();
await page.goto("https://eu.gymshark.com/es-ES/collections/all-products/mens");

const { products } = page.extract({
  products: [{
    selector: "[class*='product-card_card-wrapper']",
    limit: 3,
    fields: {
      name: { selector: "[class*='product-card_title'] a" },
      url: { selector: "a[href*='/products/']", attr: "href" }
    }
  }]
});

for (const product of products) {
  await page.goto(product.url);
  page.waitForSelector("fieldset[class*='add-to-cart_sizes']");
  const details = page.extract({
    price: { selector: "[class*='product-information_price']" },
    sizes: ["fieldset[class*='add-to-cart_sizes'] label[class*='size_size']"]
  });
  product.price = parseFloat(details.price.replace(",", "."));
  product.sizesAvailable = details.sizes;
}

return products;
