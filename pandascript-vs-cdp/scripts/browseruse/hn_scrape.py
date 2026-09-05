import json
import time


def goto(url, timeout=30.0):
    """goto_url + commit fence. The harness's polling waits race across
    navigations: the page being left still reports readyState "complete"
    and answers selector queries, so a bare goto_url followed by a poll
    can read the *previous* document. Poll until location.href leaves the
    old page before any readiness wait. (Every navigation in these scripts
    targets a different URL, which is what makes href the commit signal.)"""
    def dismiss_dialog():
        # A pending JS dialog (beforeunload/alert from the old page or an ad)
        # blocks navigation; accepting when none is open just errors, so try.
        try:
            cdp("Page.handleJavaScriptDialog", accept=True)
        except RuntimeError:
            pass

    try:
        prev = js("location.href")
    except RuntimeError:
        prev = None
    dismiss_dialog()
    goto_url(url)
    deadline = time.time() + timeout
    i = 0
    while time.time() < deadline:
        try:
            if js("location.href") != prev:
                return
        except RuntimeError:
            pass
        i += 1
        if i % 20 == 0:
            dismiss_dialog()
        time.sleep(0.05)
    raise RuntimeError(f"navigation to {url} did not commit (tab stuck on {prev})")

goto("https://news.ycombinator.com")
wait_for_load()

stories = json.loads(js("""JSON.stringify(Array.from(document.querySelectorAll("tr.athing")).slice(0, 5).map((row) => ({
  id: row.id,
  rank: row.querySelector(".rank")?.textContent ?? "",
  title: row.querySelector(".titleline > a")?.textContent ?? "",
  url: row.querySelector(".titleline > a")?.href ?? "",
})))"""))

results = []
for story in stories:
    goto(f"https://news.ycombinator.com/item?id={story['id']}")
    wait_for_element("table.fatitem")
    comments = json.loads(js("""JSON.stringify(Array.from(document.querySelectorAll("tr.comtr")).slice(0, 3).map((row) => ({
      user: row.querySelector(".hnuser")?.textContent ?? "",
      text: row.querySelector(".commtext")?.textContent ?? "",
    })))"""))
    results.append({"rank": story["rank"], "title": story["title"],
                    "url": story["url"], "comments": comments})

print(json.dumps(results))
