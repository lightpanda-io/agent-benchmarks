"""Did judging N pages in one request change the answers?

The probes record a `Judgment` per page, so the contamination question -- an
answer produced with 24 other pages in view -- is scored from what a campaign
already paid for. Three comparisons, none of which needs a new request:

  * against the per-page judgement in `*-judged.jsonl`, one request per page;
  * against the human labels, for both arms, where a row has one;
  * against the batch's own repeats, which should not disagree with themselves.

It is not a clean A/B and cannot be made into one from here: the corpus carries
each page's text capped at `result_text_budget` while the judged file was
produced by the browser at `text_prefix_cap`, so the batched arm sees less of
every page. Read a difference as a reason to run the real comparison, not as
the comparison.

    uv run --no-project python pageclass/probe_score.py \
        --samples pageclass/corpus/probe-questions.jsonl --config asked=25
"""

import argparse
import collections
import json
from pathlib import Path

import probe_common as common


def load_jsonl(path):
    for line in Path(path).read_text().splitlines():
        if line.strip():
            yield json.loads(line)


def main():
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", default=here / "corpus" / "v2.jsonl", type=Path)
    parser.add_argument("--judged", default=here / "corpus" / "v2-judged.jsonl", type=Path)
    parser.add_argument("--labels", default=here / "corpus" / "v2.labels.jsonl", type=Path)
    parser.add_argument("--samples", default=here / "corpus" / "probe-questions.jsonl", type=Path)
    parser.add_argument("--config", default="asked=25",
                        help="which configuration's answers to score")
    parser.add_argument("--all-rows", action="store_true")
    args = parser.parse_args()

    rows = common.load_rows(args.corpus, unsettled_only=not args.all_rows)
    urls = {common.page_id(i): row["url"] for i, row in enumerate(rows)}

    per_page = {r["url"]: r["klass"] for r in load_jsonl(args.judged)
                if r.get("source") == "model" and r.get("klass")}
    labels = {r["url"]: r["label"] for r in load_jsonl(args.labels)}

    repeats = collections.defaultdict(dict)
    for sample in load_jsonl(args.samples):
        if sample["config"] == args.config:
            repeats[sample["repeat"]].update(sample["answers"])
    if not repeats:
        raise SystemExit(f"{args.samples}: no samples for config {args.config!r}")

    print(f"{args.config}: {len(repeats)} repeats, {len(per_page)} per-page judgements, "
          f"{len(labels)} labels\n")

    compared = agree = scored = batch_right = page_right = 0
    unstable = []
    for pid, url in urls.items():
        answers = [repeats[r][pid]["class"] for r in sorted(repeats) if pid in repeats[r]]
        if not answers:
            continue
        if len(set(answers)) > 1:
            unstable.append((url, answers))
        choice = max(set(answers), key=answers.count)
        if url not in per_page:
            continue
        compared += 1
        agree += choice == per_page[url]
        if url in labels:
            scored += 1
            batch_right += choice == labels[url]
            page_right += per_page[url] == labels[url]
        if choice != per_page[url]:
            note = f", label {labels[url]}" if url in labels else ""
            print(f"  differs: {url[:58]:<58} batch={choice} page={per_page[url]}{note}")

    print(f"\nagreement with the per-page judgement: {agree}/{compared}")
    if scored:
        print(f"against {scored} labels: batched {batch_right}/{scored}, "
              f"per-page {page_right}/{scored}")
        print("  (a handful of rows either way is noise, not a result)")
    print(f"\nunstable across repeats: {len(unstable)}/{compared}")
    for url, answers in unstable:
        print(f"  {url[:58]:<58} {answers}")
    if unstable:
        print("  The same bytes answered differently. `judgePage` disables retries on the\n"
              "  grounds that a calibrated decoder repeats itself for the same state --\n"
              "  worth re-checking per-page before that reasoning is leaned on further.")


if __name__ == "__main__":
    main()
