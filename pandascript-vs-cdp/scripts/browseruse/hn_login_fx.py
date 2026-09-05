import json
import os
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

base = os.environ.get("BASE_URL", "http://127.0.0.1:9280")
user = os.environ["LP_HN_USERNAME"]
password = os.environ["LP_HN_PASSWORD"]

goto(f"{base}/login")
wait_for_load()

fill_input("input[name=acct]", user)
fill_input("input[name=pw]", password)
# press_key sends trusted CDP input (dispatch_key's synthetic DOM event never
# triggers implicit form submission); fill_input left focus on the pw field.
# The #logout poll is the cross-navigation wait; it only exists once logged in.
press_key("Enter")
if not wait_for_element("#logout", timeout=15):
    body = js("document.body.textContent")
    if "Validation required" in body:
        raise RuntimeError("captcha: validation required")
    raise RuntimeError("bad login" if "Bad login" in body else "login did not complete")

goto(f"{base}/user?id={user}")
wait_for_element("#hnmain table table tr:nth-child(3) td:nth-child(2)")
karma = js("""document.querySelector("#hnmain table table tr:nth-child(3) td:nth-child(2)").textContent""")

print(json.dumps({"karma": int(karma.strip())}))
