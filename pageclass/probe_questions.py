"""Probe 1: how latency scales with the question count, at a fixed state.

The state holds the same N pages throughout; only how many of them are asked
about moves, from one page (4 questions) up to all of them (4N). TypeSafe says
adding questions barely changes the response time -- this is that claim at the
scale a batched judgement would need, where it decides whether the shape is
worth its contamination risk.

The last configuration, asking about every page in the state, is the batched
design itself. Compare its latency against `probe_pages.py`'s production row
times N.

Stdlib only, so it needs no project environment.

    export TYPESAFE_API_KEY=...
    uv run --no-project python pageclass/probe_questions.py --dry-run
    uv run --no-project python pageclass/probe_questions.py \
        --pages 25 --asked 1,5,10,25 --repeats 3 \
        --out pageclass/corpus/probe-questions.jsonl
"""

import probe_common as common


def main():
    parser = common.base_parser(__doc__.splitlines()[0])
    parser.add_argument("--pages", type=int, default=25, help="pages in the state, fixed")
    parser.add_argument("--asked", default="1,5,10,25",
                        help="how many of them to ask about, comma separated")
    args = parser.parse_args()

    rows = common.load_rows(args.corpus, unsettled_only=not args.all_rows)
    pages = min(args.pages, len(rows))
    if pages < args.pages:
        print(f"corpus has {len(rows)} usable rows; state holds {pages}")
    asked_counts = sorted({min(int(a), pages) for a in args.asked.split(",") if a.strip()})

    key, base, model = common.detect()
    state_rows = rows[:pages]

    configs = [common.Config(name=f"asked={count}", pages=pages, asked=list(range(count)),
                             body=common.build_batched(state_rows, range(count), model))
               for count in asked_counts]

    if args.dry_run:
        common.dry_run(configs)

    print(f"state fixed at {pages} pages, model {model}, {args.repeats} repeats")

    session = common.Session(base, key)
    try:
        samples = common.run_campaign(session, configs, args.repeats, args.out,
                                      seed=args.seed, label="questions")
    finally:
        session.close()
    medians = common.summarize(configs, samples, claim_fixed_cost=False)

    # The state is constant here, so every token above the smallest configuration
    # was added by the questions themselves: each one repeats the whole rule text.
    # That is the number the batched design lives or dies on, and it is not the
    # fit, which cannot separate a token added by a question from one added by a
    # page.
    measured = [(c.questions, *medians[c.name][:2]) for c in configs if c.name in medians]
    if len(measured) >= 2:
        (low_q, low_tok, low_ms), (high_q, high_tok, high_ms) = measured[0], measured[-1]
        span = high_q - low_q
        if span:
            print(f"\nquestion axis, {low_q} -> {high_q} questions over an identical state:"
                  f"\n  {(high_ms - low_ms) / span:+.1f} ms and {(high_tok - low_tok) / span:+.0f} "
                  f"input tokens per question")
            print(f"  ({high_tok / max(low_tok, 1) - 1:+.0%} tokens for the same pages -- "
                  f"a question carries its own copy of the rules)")


if __name__ == "__main__":
    main()
