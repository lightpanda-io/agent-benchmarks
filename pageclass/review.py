#!/usr/bin/env python3
"""Label the rows a harvest could not settle on its own.

    uv run --no-project python pageclass/review.py corpus/v1.jsonl

Writes `<corpus>.labels.jsonl`, append-only and keyed by url, so an interrupted
pass keeps its work and a second pass only sees what is still unlabelled. The
corpus file is never rewritten.

The rule's own verdict is shown last and dimmed, deliberately: reading it first
is how a reviewer talks themselves into agreeing with the thing being measured.
"""

import argparse
import json
import pathlib

CLASSES = [
    "content", "not_found", "empty", "login_required", "captcha",
    "bot_blocked", "geo_blocked", "rate_limited", "consent_wall",
    "server_error", "loading",
]

DIM, RESET, BOLD = "\033[2m", "\033[0m", "\033[1m"


def load(path: pathlib.Path) -> list[dict]:
    rows = []
    for line in path.open():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus")
    parser.add_argument("--by", default="human",
                        help="provenance recorded on each label. Use something "
                             "other than `human` when the labeller is not one: a "
                             "corpus scored against labels the model under test "
                             "produced is marking its own homework, and the "
                             "provenance is what lets a spot-check find those rows.")
    args = parser.parse_args()
    corpus = pathlib.Path(args.corpus)
    labels_path = corpus.with_suffix(".labels.jsonl")

    done = {}
    if labels_path.exists():
        for row in load(labels_path):
            done[row["url"]] = row["label"]

    rows = load(corpus)
    todo = [r for r in rows
            if not r.get("error") and not r.get("label") and r["url"] not in done]

    auto = sum(1 for r in rows if r.get("confirmed") == "auto")
    print(f"{len(rows)} row(s): {auto} auto-accepted, {len(done)} reviewed, "
          f"{len(todo)} to go\n")

    menu = "  ".join(f"{i}={c}" for i, c in enumerate(CLASSES, 1))

    with labels_path.open("a") as sink:
        for n, row in enumerate(todo, 1):
            signals = row.get("signals") or {}
            print(f"{BOLD}[{n}/{len(todo)}]{RESET} {row['url']}")
            print(f"  probe={row['probe']}  status={signals.get('status')}  "
                  f"title={signals.get('title')!r}")
            text = (signals.get("text") or "").strip()
            print(f"  {text[:400]}{'…' if len(text) > 400 else ''}")
            if row.get("proposed"):
                print(f"  {BOLD}proposed: {row['proposed']}{RESET} (Enter accepts)")
            rule = row.get("rule") or {}
            print(f"  {DIM}rules said {rule.get('class')} — {rule.get('reason')}{RESET}")
            print(f"  {DIM}{menu}{RESET}")

            while True:
                try:
                    answer = input("  label> ").strip().lower()
                except EOFError:
                    print("\nstopping")
                    return 0
                if answer in ("q", "quit"):
                    print("stopping")
                    return 0
                if answer in ("s", "skip"):
                    label = None
                    break
                if answer == "" and row.get("proposed"):
                    label = row["proposed"]
                    break
                if answer.isdigit() and 1 <= int(answer) <= len(CLASSES):
                    label = CLASSES[int(answer) - 1]
                    break
                matches = [c for c in CLASSES if c.startswith(answer)] if answer else []
                if len(matches) == 1:
                    label = matches[0]
                    break
                print(f"  {DIM}pick a number, an unambiguous prefix, s to skip, "
                      f"q to stop{RESET}")

            if label is not None:
                sink.write(json.dumps({"url": row["url"], "label": label,
                                       "by": args.by,
                                       "agrees_with_rules":
                                           label == (row.get("rule") or {}).get("class")}) + "\n")
                sink.flush()
                print(f"  -> {label}\n")
            else:
                print("  -> skipped\n")

    print(f"labels in {labels_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
