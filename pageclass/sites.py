"""The probe set.

Each site contributes up to three probes, and only the two manipulated ones
carry a proposed label:

  home    the site's own entry page. NO proposal — a homepage's class is a
          property of the site, not of our request. flights.google.com serves a
          consent wall at 200, so "homepage means content" is simply false.
  uuid    <origin>/<uuid4>, which cannot exist. Proposes not_found, and the
          proposal is auto-accepted only when the status corroborates it.
  search  a nonsense query through a known search URL. Proposes empty.

`search` is a template with one `{q}` placeholder, given only where the URL
shape is stable and public. Sites without one contribute two probes.
"""

SITES = [
    # Scraping sanctioned practice targets — stable, and the only ones whose
    # search shape is guaranteed not to move under us.
    {"host": "quotes.toscrape.com", "search": "https://quotes.toscrape.com/tag/{q}/"},
    {"host": "books.toscrape.com"},
    # Docs and reference: long text, no commerce, rarely walled.
    {"host": "developer.mozilla.org", "search": "https://developer.mozilla.org/en-US/search?q={q}"},
    {"host": "docs.python.org", "search": "https://docs.python.org/3/search.html?q={q}"},
    {"host": "ziglang.org"},
    {"host": "en.wikipedia.org", "search": "https://en.wikipedia.org/w/index.php?search={q}"},
    {"host": "man7.org"},
    {"host": "rfc-editor.org"},
    # News and aggregators: link-dense, some consent-walled in the EU.
    {"host": "news.ycombinator.com"},
    {"host": "lobste.rs"},
    {"host": "bbc.com"},
    {"host": "theguardian.com"},
    {"host": "reuters.com"},
    {"host": "lemonde.fr"},
    {"host": "spiegel.de"},
    # Retail: where soft 404s and consent walls both live.
    {"host": "allbirds.com"},
    {"host": "gymshark.com"},
    {"host": "patagonia.com"},
    {"host": "ikea.com"},
    {"host": "zalando.co.uk"},
    {"host": "etsy.com"},
    # Search and portals: the consent-wall heartland.
    {"host": "duckduckgo.com", "search": "https://duckduckgo.com/?q={q}"},
    {"host": "google.com"},
    {"host": "flights.google.com"},
    {"host": "bing.com"},
    {"host": "yahoo.com"},
    # Developer platforms: login walls on deep paths, rate limits under load.
    {"host": "github.com", "search": "https://github.com/search?q={q}"},
    {"host": "gitlab.com"},
    {"host": "stackoverflow.com"},
    {"host": "pypi.org", "search": "https://pypi.org/search/?q={q}"},
    {"host": "crates.io"},
    {"host": "npmjs.com"},
]

#: A query with no plausible hits. Long and unpronounceable on purpose: a short
#: nonsense word can still match a product code or a username.
NONSENSE_QUERY = "zqxjvbnmkwlrtpfdghs-no-such-thing-84619"
