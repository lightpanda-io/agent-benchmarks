"""
AssistantBench runner for the Lightpanda agent.

Loads the AssistantBench dataset from HuggingFace, invokes the `lightpanda
agent --task` one-shot mode per row, captures stdout as the predicted answer,
and writes predictions + scores to a timestamped results directory.

Example:

    # Smoke test
    uv run assistantbench-run --limit 3

    # Full dev run
    uv run assistantbench-run --split validation
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from datasets import load_dataset  # type: ignore[import-not-found]

from ..common import (
    add_common_runner_args,
    emit_scores,
    extract_answer_envelope,
    load_completed_ids,
    print_lightpanda_missing,
    resolve_lightpanda_binary,
    resolve_out_dir,
    run_benchmark_tasks,
    run_lightpanda_task,
    write_run_manifest,
)
from .grade import grade_predictions

# benchmarks/src/agent_benchmarks/assistantbench/run.py → benchmarks/
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Override Agent.zig's tool-use-oriented default with a research-oriented
# system prompt tailored to the benchmark. Preserves the load-bearing CSS
# selector rules so tool use still works.
SYSTEM_PROMPT = """\
You are a research assistant driving the Lightpanda headless browser on an open-web QA benchmark.

The Lightpanda browser tools are the ONLY way you can access the web. There is no WebSearch, no WebFetch, no shortcut — you must navigate real pages. Your tool surface includes `search`, `goto`, `tree`, `markdown`, `extract`, `structuredData`, `findElement`, `interactiveElements`, `links`, `click`, `fill`, `hover`, `selectOption`, `setChecked`, `press`, `scroll`, `waitForSelector`, `nodeDetails`, `getUrl`, `eval`, `consoleLogs`, `detectForms`.

BE PERSISTENT — this is the load-bearing instruction:
- This benchmark expects multi-step browsing. Most tasks need 20-50 tool calls; some need 100+. Answers from prior knowledge without browsing score 0.
- If a search returns poor results, try DIFFERENT phrasings — synonyms, narrower queries, different angles. Don't repeat the same query.
- If a page is unreachable, find a DIFFERENT source. Wikipedia, official sites, news outlets, Yelp, store directories — be creative.
- If extraction fails, try a different tool (markdown → tree → extract → structuredData → findElement).
- Do NOT respond "unknown" or fall back to prior knowledge until you have made at least 20 substantive tool calls AND tried at least 3 different sources/angles.
- Small-candidate questions ("A, B, or C", yes/no): always pick one — never abstain.

Strategy:
1. Plan: identify the most authoritative source. Prefer direct sites (Wikipedia, official, retailer) over search-engine results when you know the source.
2. Search: use the `search` tool — do NOT goto google.com directly. With `TAVILY_API_KEY` set, `search` queries Tavily and returns a clean numbered list of {title, url, snippet}; without the key, it falls back to scraping the DuckDuckGo HTML endpoint. Google scraping is blocked by Lightpanda's User-Agent and TLS fingerprint.
3. Navigate: open the source, inspect, extract.
4. Re-inspect after page-changing actions — DOM snapshots and node ids go stale.
5. Cross-check on a second source where the gold answer is non-obvious (lists, numerical estimates).

Final-answer envelope — STRICT
================================
Your entire response will be discarded except for the LAST text wrapped in `<ANSWER>...</ANSWER>` tags. Reasoning, tool-call narration, partial-credit notes — anything outside the envelope — is ignored. Only the envelope contents are graded.

Format INSIDE the envelope:
- No preface, no explanation, no markdown, no source citations.
- Numbers: bare digits only — no units, no `$`, no comma separators.
- Names/titles: complete and verbatim, no decoration.
- URLs: bare URL only.
- Lists: one item per line, nothing else.
- JSON dicts: one JSON object per line, no surrounding prose.

Examples (good):
  <ANSWER>45</ANSWER>
  <ANSWER>Oko, Thief of Crowns</ANSWER>
  <ANSWER>https://example.com/foo</ANSWER>
  <ANSWER>CrossFit East River
  Avea Pilates</ANSWER>

Examples (bad — these all score 0):
  Based on my research, <ANSWER>45</ANSWER>           ← prose outside is fine, but…
  <ANSWER>**45**</ANSWER>                              ← markdown inside
  <ANSWER>The answer is 45.</ANSWER>                   ← preface inside
  <ANSWER>$45</ANSWER>                                 ← currency
  <ANSWER>~45</ANSWER>                                 ← approximation marker

Tool-use rules:
- Never use backendNodeId with click, fill, hover, selectOption, or setChecked. Always use a CSS selector.
- Use findElement to resolve a description into a selector when needed.
- Use distinguishing attributes (value, name, position) so selectors are unique.
- For credentials, pass $LP_USERNAME / $LP_PASSWORD directly as values.
"""

TASK_PROMPT_TEMPLATE = (
    "{task}\n\n"
    "Wrap your final answer in <ANSWER>...</ANSWER>. For a list, one item per line "
    "inside the envelope. For a number, bare digits only inside the envelope."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_runner_args(parser, suite_name="assistantbench")
    args = parser.parse_args(argv)

    lightpanda = resolve_lightpanda_binary(args.lightpanda, PROJECT_ROOT)
    if not lightpanda.exists():
        print_lightpanda_missing(lightpanda)
        return 2

    out_dir = resolve_out_dir(args.out_dir, PROJECT_ROOT, "assistantbench")
    predictions_path = out_dir / "predictions.jsonl"
    write_run_manifest(out_dir, agent_provider=args.provider, agent_model=args.model)

    completed = load_completed_ids(predictions_path) if args.resume else set()
    if completed:
        print(f"Resuming — skipping {len(completed)} already-completed task(s)", file=sys.stderr)

    print(f"Loading AssistantBench/{args.split} from HuggingFace...", file=sys.stderr)
    ds = load_dataset("AssistantBench/AssistantBench", split=args.split)
    rows: list[dict[str, Any]] = list(ds)
    if args.limit is not None:
        rows = rows[: args.limit]

    pending = [r for r in rows if r["id"] not in completed]

    def _work(row: dict[str, Any]) -> dict[str, Any]:
        pred, duration_s, timed_out, stderr_tail, rc, trace, usage = run_lightpanda_task(
            lightpanda=lightpanda,
            provider=args.provider,
            model=args.model,
            user_agent=args.user_agent,
            system_prompt=SYSTEM_PROMPT,
            task_prompt=TASK_PROMPT_TEMPLATE.format(task=row["task"]),
            timeout_s=args.timeout,
        )
        pred, envelope_note = extract_answer_envelope(pred)
        if envelope_note:
            stderr_tail = (stderr_tail or "") + f"\n[envelope]\n{envelope_note}\n"
        return {
            "id": row["id"],
            "task": row["task"],
            "gold": row["answer"],
            "prediction": pred,
            "duration_s": round(duration_s, 2),
            "timed_out": timed_out,
            "returncode": rc,
            "difficulty": row.get("difficulty"),
            "trace": trace,
            "usage": usage,
            "stderr_tail": stderr_tail,
        }

    run_benchmark_tasks(
        pending,
        _work,
        predictions_path=predictions_path,
        workers=args.workers,
        timeout_s=args.timeout,
        provider=args.provider,
        model=args.model,
        preview_fn=lambda row: row["task"],
    )

    print("\nGrading...", file=sys.stderr)
    emit_scores(grade_predictions(predictions_path), out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
