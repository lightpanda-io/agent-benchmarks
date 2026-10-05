"""One HTTP client for every provider in the loop.

Retries are counted and their wait is measured, because they are not free: they
sit inside the run's clock. The Vercel AI Gateway in particular returns 503 for
large System One bodies -- probabilistically, not at a fixed size: the same
8.5 KB request has been seen to return 200 and then 503 -- so a campaign run
through the gateway must report how often it retried, and a direct
TYPESAFE_API_KEY is the better path for link-dense pages.
"""

import random
import time

import httpx

CLIENT = httpx.Client(http2=True, timeout=45)
RETRY = {408, 429, 500, 502, 503, 504, 529}
ATTEMPTS = 5

STATS = {"retries": 0, "retry_ms": 0.0, "last_status": None, "shrinks": 0, "min_scale": 1.0}


def reset():
    STATS.update(retries=0, retry_ms=0.0, last_status=None, shrinks=0, min_scale=1.0)


def post_json(url, key, body, shrink=None):
    """`shrink(attempt)` rebuilds a smaller body for the next try.

    A 503 here is a size complaint, so retrying the identical request mostly
    fails the same way. When the caller can express the same question in fewer
    bytes, do that instead and record that the request was degraded.
    """
    for attempt in range(ATTEMPTS):
        try:
            response = CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as exc:
            # A dropped connection is transport, not a decision. Retry it on the
            # same schedule as a 503 rather than failing the run.
            if attempt == ATTEMPTS - 1:
                raise RuntimeError(f"model connection failed: {exc}") from None
            wait = 0.5 * 2**attempt + random.random() * 0.25
            STATS["retries"] += 1
            STATS["retry_ms"] += wait * 1000
            time.sleep(wait)
            continue
        STATS["last_status"] = response.status_code
        if response.status_code in RETRY and attempt < ATTEMPTS - 1:
            wait = 0.5 * 2**attempt + random.random() * 0.25
            STATS["retries"] += 1
            STATS["retry_ms"] += wait * 1000
            time.sleep(wait)
            if shrink is not None:
                smaller, scale = shrink(attempt)
                if smaller is not None:
                    body = smaller
                    STATS["shrinks"] += 1
                    STATS["min_scale"] = min(STATS["min_scale"], scale)
            continue
        if response.is_error:
            raise RuntimeError(
                f"provider returned HTTP {response.status_code}: {response.text[:200]}")
        return response.json()
    raise RuntimeError(f"model unavailable after {ATTEMPTS} attempts")
