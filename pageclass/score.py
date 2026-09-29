#!/usr/bin/env python3
"""Score a labelled corpus: arm D against arm D+J.

    uv run --no-project python pageclass/score.py corpus/v1.jsonl [corpus/v1-judged.jsonl]

One corpus scores the rules alone. Pass a second, harvested with --judge over
the same urls, to get the comparison the kill bar is set against: accuracy on
the rows where the status did NOT settle the page, which is the only ground a
rule cannot reach.
"""

import collections
import json
import pathlib
import sys


def load(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.open() if line.strip()]


def labels_for(corpus: pathlib.Path) -> dict[str, str]:
    """Auto-accepted labels from the corpus, plus anything review.py confirmed."""
    out = {}
    for row in load(corpus):
        if row.get("label"):
            out[row["url"]] = row["label"]
    side = corpus.with_suffix(".labels.jsonl")
    if side.exists():
        for row in load(side):
            out[row["url"]] = row["label"]
    return out


def report(name: str, pairs: list[tuple[str, str]]) -> None:
    """pairs is (truth, predicted)."""
    if not pairs:
        print(f"{name}: nothing to score")
        return
    hits = sum(1 for t, p in pairs if t == p)
    print(f"\n{name}: {hits}/{len(pairs)} = {hits / len(pairs):.1%}")

    classes = sorted({t for t, _ in pairs} | {p for _, p in pairs})
    tp = collections.Counter()
    fp = collections.Counter()
    fn = collections.Counter()
    for truth, pred in pairs:
        if truth == pred:
            tp[truth] += 1
        else:
            fp[pred] += 1
            fn[truth] += 1

    print(f"  {'class':16s} {'n':>4s} {'prec':>6s} {'rec':>6s}")
    for c in classes:
        support = tp[c] + fn[c]
        prec = tp[c] / (tp[c] + fp[c]) if tp[c] + fp[c] else float("nan")
        rec = tp[c] / support if support else float("nan")
        print(f"  {c:16s} {support:4d} {prec:6.2f} {rec:6.2f}")

    confused = collections.Counter((t, p) for t, p in pairs if t != p)
    if confused:
        print("  worst confusions:")
        for (truth, pred), n in confused.most_common(6):
            print(f"    {truth} read as {pred}: {n}")


def settled_by_status(row: dict) -> bool:
    rule = row.get("rule") or {}
    return rule.get("needsJudgment") is False


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    rules_path = pathlib.Path(sys.argv[1])
    truth = labels_for(rules_path)
    rules_rows = [r for r in load(rules_path) if not r.get("error")]

    errors = sum(1 for r in load(rules_path) if r.get("error"))
    unlabelled = [r for r in rules_rows if r["url"] not in truth]
    print(f"{len(rules_rows)} usable row(s), {errors} error(s), "
          f"{len(truth)} labelled, {len(unlabelled)} unlabelled")
    if unlabelled:
        print("  run review.py — unlabelled rows are excluded from every figure below")

    scored = [r for r in rules_rows if r["url"] in truth]
    report("arm D, every labelled row", [(truth[r["url"]], r["klass"]) for r in scored])

    open_rows = [r for r in scored if not settled_by_status(r)]
    report("arm D, rows the status did not settle",
           [(truth[r["url"]], r["klass"]) for r in open_rows])

    if len(sys.argv) < 3:
        print("\nno judged corpus given; pass one to compare the arms")
        return 0

    judged = {r["url"]: r for r in load(pathlib.Path(sys.argv[2])) if not r.get("error")}
    both = [r for r in open_rows if r["url"] in judged]
    if not both:
        print("\nthe judged corpus shares no unsettled urls with this one")
        return 1

    print(f"\n--- the comparison, on {len(both)} unsettled row(s) present in both ---")
    report("arm D", [(truth[r["url"]], r["klass"]) for r in both])
    report("arm D+J", [(truth[r["url"]], judged[r["url"]]["klass"]) for r in both])

    asked = [judged[r["url"]] for r in both if judged[r["url"]].get("judgment")]
    if asked:
        latency = sorted(j["judgment"]["latencyMs"] for j in asked)
        tokens = [j["judgment"]["inputTokens"] for j in asked]
        print(f"\nmodel arm: {len(asked)} call(s), "
              f"median {latency[len(latency) // 2]}ms, "
              f"{sum(tokens) / len(tokens):.0f} input tokens mean, "
              f"${sum(tokens) / 1e6 * 0.042:.5f} total at list price")
        share = len(asked) / len(load(pathlib.Path(sys.argv[2])))
        print(f"reached the model arm: {share:.0%} of harvested rows")

        low = [j for j in asked if j["judgment"]["confidence"] < 0.7]
        if low:
            wrong = sum(1 for j in low if truth.get(j["url"]) != j["klass"])
            print(f"confidence below 0.70: {len(low)} row(s), {wrong} of them wrong "
                  f"— the gate a cascade would threshold on")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
