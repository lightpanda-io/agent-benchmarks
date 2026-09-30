"""Probe 2: how latency scales with the state, at a fixed question count.

Every configuration asks the same four questions about the same first page; only
the number of *other* pages sitting in the state moves. That isolates ingest
cost, which is the term batching cannot avoid: one request carrying N pages
ingests exactly what N requests carrying one page each do.

If latency is flat in state size, batching is nearly free and the round trip is
the whole cost. If it is linear, batching saves only the fixed per-request cost.
If it is superlinear, batching is a penalty and concurrency is the answer.

Stdlib only, so it needs no project environment -- unlike `harvest.py`, nothing
here drives a browser.

    export TYPESAFE_API_KEY=...
    uv run --no-project python pageclass/probe_pages.py --dry-run
    uv run --no-project python pageclass/probe_pages.py \
        --sizes 1,5,10,25 --repeats 3 --out pageclass/corpus/probe-pages.jsonl
"""

import probe_common as common


def main():
    parser = common.base_parser(__doc__.splitlines()[0])
    parser.add_argument("--sizes", default="1,5,10,25",
                        help="pages in the state, comma separated")
    parser.add_argument("--skip-connect", action="store_true",
                        help="skip the cold-vs-warm connection measurement")
    args = parser.parse_args()

    sizes = sorted({int(s) for s in args.sizes.split(",") if s.strip()})
    rows = common.load_rows(args.corpus, unsettled_only=not args.all_rows)
    if max(sizes) > len(rows):
        print(f"corpus has {len(rows)} usable rows; clamping sizes to it")
        sizes = sorted({min(size, len(rows)) for size in sizes})

    key, base, model = common.detect()

    configs = [common.Config(name="production", pages=1, asked=[0], batched=False,
                             body=common.build_production(rows[0], model))]
    configs += [common.Config(name=f"state={size}", pages=size, asked=[0],
                              body=common.build_batched(rows[:size], [0], model))
                for size in sizes]

    if args.dry_run:
        common.dry_run(configs)

    print(f"{len(rows)} usable rows, model {model}, {args.repeats} repeats")

    if not args.skip_connect:
        connect, warm = common.connect_overhead(base, key, configs[0].body)
        print(f"connection: {connect:.0f} ms to establish, {warm:.0f} ms for a request on it "
              f"-> {connect:.0f} ms per judgement for a client that does not pool\n")

    session = common.Session(base, key)
    try:
        samples = common.run_campaign(session, configs, args.repeats, args.out,
                                      seed=args.seed, label="pages")
    finally:
        session.close()
    common.summarize(configs, samples)


if __name__ == "__main__":
    main()
