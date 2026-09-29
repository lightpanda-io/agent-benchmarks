"""The four workflows, each with a checker that does not consult the model.

DONE is the model's opinion. Whether a run counts is decided here, against a
fresh observation taken after the clock stops.
"""

import re
import urllib.request
from dataclasses import dataclass, field, replace


@dataclass
class Task:
    id: str
    goal: str
    url: str = ""
    fixture: bool = False
    setup: object = None
    check: object = field(default=None)

    def instruction(self, context):
        return self.goal.format(**context) if context else self.goal

    def verify(self, state, context):
        # Lightpanda reads the page back as markdown and Chrome as innerText,
        # so the checker sees one escaped and the other not. Both arms must be
        # judged against the same evidence.
        plain = replace(state, text=re.sub(r"\\([^0-9A-Za-z\s])", r"\1", state.text))
        checks = self.check(plain, context)
        return {"passed": all(checks.values()), "checks": checks}


def _hotel(state, _context):
    return {
        "detail_page": "Casa Flora" in state.text,
        "free_cancellation": "Free cancellation included" in state.text,
        "category_filter": "Your filters: Design" in state.text,
        "cancellation_filter": "Free cancellation enabled" in state.text,
        "destination_filter": "Destination Lisbon" in state.text,
    }


def _quotes(state, _context):
    return {
        "results_page": state.url.endswith("/filter.aspx"),
        "author": "Albert Einstein" in state.text,
        "tag": "deep-thoughts" in state.text,
        "quote": "The world as we have created it is a process of our thinking" in state.text,
    }


def _wikipedia(state, _context):
    return {
        "article": state.url.startswith("https://en.wikipedia.org/wiki/")
        and "incompleteness_theorems" in state.url,
        "title": "incompleteness theorems" in state.title,
    }


def _hn_setup():
    """Read the front page ourselves, before the run, so the expected answer
    does not come from the browser under test."""
    request = urllib.request.Request(
        "https://news.ycombinator.com/", headers={"User-Agent": "lightpanda-ultrafast-bench"})
    with urllib.request.urlopen(request, timeout=20) as response:
        page = response.read().decode(errors="ignore")
    match = re.search(r'<tr class="athing[^"]*" id="(\d+)"', page)
    if not match:
        raise RuntimeError("could not read the top story id from the Hacker News front page")
    title = re.search(r'<span class="titleline"><a [^>]*>(.*?)</a>', page)
    return {"item": match.group(1), "title": re.sub("<[^>]+>", "", title.group(1)) if title else ""}


def _hn(state, context):
    return {
        "item_page": f"item?id={context['item']}" in state.url,
        "comments": "comments" in state.text.lower() or "add comment" in state.text.lower(),
    }


def _hostelworld_setup():
    """Resolve the expected hostel ourselves, before the run, so the answer does
    not come from the browser under test."""
    request = urllib.request.Request(
        "https://www.hostelworld.com/hostels/north-america/usa/san-francisco/f/parking/",
        headers={"User-Agent": "lightpanda-ultrafast-bench"})
    with urllib.request.urlopen(request, timeout=30) as response:
        page = response.read().decode(errors="ignore")
    match = re.search(r"/hostels/p/(\d+)/([a-z0-9-]+)/", page)
    if not match:
        raise RuntimeError("could not read a hostel from the parking-filtered list")
    return {"id": match.group(1), "slug": match.group(2)}


def _hostelworld(state, context):
    return {
        "hostel_page": f"/hostels/p/{context['id']}/" in state.url,
        "loaded": len(state.text.strip()) > 200,
    }


TASKS = {t.id: t for t in [
    Task(
        id="hotel",
        fixture=True,
        goal="Search for stays in Lisbon, set the stay category to Design, switch on free "
             "cancellation, and then open Casa Flora. Stop when its page is open.",
        check=_hotel,
    ),
    Task(
        id="quotes",
        url="https://quotes.toscrape.com/search.aspx",
        goal="Filter the quotes by author Albert Einstein and tag deep-thoughts, then run "
             "the search. Stop when the matching quote is visible.",
        check=_quotes,
    ),
    Task(
        id="wikipedia",
        url="https://en.wikipedia.org/wiki/Main_Page",
        goal="Open the Wikipedia article on Godel's incompleteness theorems. "
             "Stop when the article itself is open.",
        check=_wikipedia,
    ),
    Task(
        id="hn",
        url="https://news.ycombinator.com/",
        goal="Open the comments page of the story titled \"{title}\". "
             "Stop when that story's comments page is open.",
        setup=_hn_setup,
        check=_hn,
    ),
    Task(
        id="hostelworld",
        url="https://www.hostelworld.com/hostels/north-america/usa/san-francisco/",
        goal="Show only the San Francisco hostels that have parking, then open the page of "
             "the hostel that matches. Stop when that hostel's page is open.",
        setup=_hostelworld_setup,
        check=_hostelworld,
    ),
]}
