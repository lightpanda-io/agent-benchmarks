# BU Bench V1

browser-use's [100-task benchmark](https://github.com/browser-use/benchmark),
run through `lightpanda agent --task`. The tasks are decrypted in memory from
upstream's `BU_Bench_V1.enc`, and every run is judged with upstream's
`judge.py` on `gemini-2.5-flash`, the judge behind its published table. Both
are imported from a checkout, not ported.

```bash
git clone https://github.com/browser-use/benchmark .bu-bench
git -C .bu-bench checkout 1e0e2f1ed12d3dfbdfab6b7bff415ec090ff9f74

uv run bubench-run --provider gemini --model gemini-3.8-flash \
    --search-engine keenable --block-answer-sources \
    --lightpanda path/to/frozen/lightpanda --workers 2

# Re-judge a finished run
uv run bubench-grade results/bubench/<ts>/predictions.jsonl
```

`GOOGLE_API_KEY` is needed for the judge. `--limit 10` takes one task per
category in turn; `--category` picks categories.

## How a run matches browser-use's

- The agent gets the task text verbatim and its default system prompt.
- The judge reads the final answer and the steps, formatted the way
  browser-use's `agent_steps()` formats them. It gets no screenshots. It
  keeps only the first 40,000 characters of the steps, so every result is
  cut to one common length that fits, and no step is dropped.
- Step results come from the `--trace` file, which needs a binary with
  `--trace-results` (lightpanda-io/browser#3915). Without it, a run fails
  on the unknown flag.
- `search` runs on Keenable's keyless endpoint: the runner removes the
  Brave, Tavily, Exa and Keenable keys from the agent's environment.

## Caveats

- The agent has no file upload, so the InteractionTests tasks that need one
  fail.
- Sites with bot walls (nature.com, APA, barrons.com, streeteasy.com) block
  more as runs from the same IP pile up: a third back-to-back run lost 4
  tasks the first two passed. Space runs out.
- `predictions.jsonl` holds the decrypted tasks and answers, which upstream
  asks not to publish. `results/` stays out of git.
