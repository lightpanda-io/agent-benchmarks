#!/usr/bin/env python3
"""Harvest pageClass reports into a corpus, one JSONL row per probe.

    uv run --group pageclass python pageclass/harvest.py --out corpus/v1.jsonl

Runs with `judge: false` by default, so a harvest costs no tokens and records
only what the rules concluded. `--judge` adds the model's answer to each row.
Rows are appended as they arrive, so an interrupted run keeps what it got.
"""

import argparse
import json
import os
import pathlib
import time
import uuid

from mcp import Mcp
from sites import NONSENSE_QUERY, SITES

#: Auto-accept a `not_found` proposal only on these. A soft 404 returns 200 and
#: is indistinguishable from content to the rules, which is the whole point, so
#: it has to be confirmed by eye.
HARD_NOT_FOUND = {404, 410}


def probes(site: dict) -> list[dict]:
    host = site["host"]
    out = [{"probe": "home", "url": f"https://{host}/", "proposed": None}]
    out.append({
        "probe": "uuid",
        "url": f"https://{host}/{uuid.uuid4()}",
        "proposed": "not_found",
    })
    if template := site.get("search"):
        out.append({
            "probe": "search",
            "url": template.format(q=NONSENSE_QUERY),
            "proposed": "empty",
        })
    return out


def confirm(proposed: str | None, report: dict) -> tuple[str | None, str | None]:
    """(label, confirmed_by). A proposal the status corroborates needs no human;
    everything else stays unlabelled until review.py."""
    if proposed != "not_found":
        return None, None
    status = report.get("signals", {}).get("status")
    if status in HARD_NOT_FOUND and report.get("class") == "not_found":
        return "not_found", "auto"
    return None, None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="corpus/v1.jsonl")
    parser.add_argument("--binary", default=os.environ.get("LIGHTPANDA_BIN"))
    parser.add_argument("--judge", action="store_true",
                        help="also ask the decision model (costs tokens)")
    parser.add_argument("--pace", type=float, default=1.5,
                        help="seconds between requests to one host")
    parser.add_argument("--only", help="substring filter over hosts")
    args = parser.parse_args()

    if not args.binary:
        parser.error("--binary, or LIGHTPANDA_BIN in the environment")

    sites = [s for s in SITES if not args.only or args.only in s["host"]]
    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    seen = set()
    if out_path.exists():
        for line in out_path.open():
            try:
                seen.add(json.loads(line)["url"])
            except (json.JSONDecodeError, KeyError):
                pass
        print(f"resuming: {len(seen)} row(s) already in {out_path}")

    written = 0
    with out_path.open("a") as sink, Mcp(args.binary) as lp:
        for site in sites:
            for probe in probes(site):
                if probe["url"] in seen:
                    continue
                report = lp.page_class(probe["url"], judge=args.judge)
                row = {**probe, "host": site["host"]}
                if isinstance(report, str):
                    row.update(error=report, label=None, confirmed=None)
                else:
                    label, by = confirm(probe["proposed"], report)
                    row.update(
                        error=None,
                        label=label,
                        confirmed=by,
                        klass=report.get("class"),
                        source=report.get("source"),
                        rule=report.get("rule"),
                        judgment=report.get("judgment"),
                        unjudged=report.get("unjudged"),
                        signals=report.get("signals"),
                    )
                sink.write(json.dumps(row) + "\n")
                sink.flush()
                written += 1
                mark = row.get("confirmed") or "-"
                print(f"{mark:4s} {row.get('klass') or 'ERR':15s} {probe['url'][:78]}")
                time.sleep(args.pace)

    print(f"\n{written} row(s) written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
