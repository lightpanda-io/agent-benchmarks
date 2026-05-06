"""Shared LLM-judge grader for text-only browser-agent benchmarks.

Originally extracted from `webvoyager/grade.py`. Both `webvoyager` and
`webbench` consume this — the prediction envelope they emit shares the
same shape (`id`, `web_name`, `task`, `start_url`, `prediction`, `trace`,
optional `reference`), so a single judge implementation works for both.

The judge dispatches on model prefix: `claude-*` → Anthropic, `gemini-*` →
Google. Default is `claude-sonnet-4-5` because the v3Evaluator-shaped
rubric is well-tuned for Claude, and because the agent under test
typically runs on Gemini — different families avoid same-family self-eval.

Callers parameterize `variant` (e.g. `"text-only"`, `"webbench-text-only"`)
so the resulting `scores.json` records which protocol it was run under.
Comparability requires both `judge_model` and `variant` to match.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import sys
from pathlib import Path
from typing import Any

from .common import PAGE_SNAPSHOT_TOOLS, mean

DEFAULT_JUDGE_MODEL = "claude-sonnet-4-5"

# How many "text snapshots" of what the agent saw to show the judge. Each
# snapshot is a single page-reading tool output (markdown / tree / extract /
# ...). Lightpanda's Terminal.printToolResult caps each stderr result at 500
# chars, so these are excerpts rather than full page contents — the judge
# should treat them as evidence, not as authoritative. Five snapshots keeps
# the prompt under ~4 KB including overhead.
N_SNAPSHOTS_TO_SHOW = 8
SNAPSHOT_MAX_CHARS = 2000

JUDGE_SYSTEM_PROMPT = """\
You are an expert evaluator judging whether a browser agent successfully completed a web-navigation task. Lightpanda is a text-only browser (no rendering), so instead of screenshots you are shown excerpts of what the agent's page-reading tools actually returned — these are textual substitutes for screenshots.

You are given:
  1. The task and its start URL.
  2. The list of URLs the agent visited.
  3. The most recent text snapshots of pages the agent observed (via markdown / tree / extract / interactiveElements tool outputs). Each excerpt is capped at ~500-900 chars and may be truncated.
  4. The agent's final natural-language answer.
  5. Optionally, a reference answer (not ground truth — one valid outcome among many).

Decide if the task was completed correctly. Consider:
  - Does the answer address the task directly, with concrete specifics?
  - Is the answer *grounded in the page snapshots*? Entities, numbers, ratings, titles, prices the agent reports should be visible in the excerpts, or the claim is suspect.
  - Are the URLs plausible for this task?
  - Did the agent actually reach a relevant page, or did it thrash on errors / cookie walls / access blocks and then give up?

Trust signals FOR success: the claimed answer is visible in a snapshot from a plausible URL; the agent named something concrete and the excerpt confirms it.
Trust signals AGAINST success: the answer is generic ("I couldn't find..."), hedged, or cites details NOT visible in any snapshot; the snapshots show access-restriction / cookie / 404 pages; URLs are not plausible for the task.

Respond in this exact two-line format:

VERDICT: YES
REASONING: <one short sentence, ideally citing a snapshot or the URL that grounds the verdict>

or:

VERDICT: NO
REASONING: <one short sentence>

Use YES only when you are confident the task was successfully accomplished. Use NO for partial success, ungrounded claims, generic non-answers, wrong results, or when the agent gave up. Do not hedge with MAYBE or PARTIAL — the verdict is binary."""


def _summarize_args(tool: str, args: Any) -> str:
    """Produce a short label for a tool call to put next to its snapshot —
    e.g. `extract(.recipe-title)` or `markdown()`. Falls back to raw JSON
    when the arg shape is unexpected."""
    if not isinstance(args, dict):
        return ""
    if tool == "extract":
        sel = args.get("selector") or args.get("css") or ""
        return f"selector={sel!r}" if sel else ""
    if tool == "goto":
        return args.get("url", "")
    if tool == "tree":
        depth = args.get("maxDepth")
        return f"maxDepth={depth}" if depth is not None else ""
    return json.dumps(args) if args else ""


def _format_prompt(pred: dict[str, Any]) -> str:
    trace = pred.get("trace") or []

    urls = [
        entry["args"].get("url", "")
        for entry in trace
        if entry.get("tool") == "goto" and isinstance(entry.get("args"), dict)
    ]
    urls = [u for u in urls if u]
    urls_block = "\n".join(f"  - {u}" for u in urls) if urls else "  (no goto tool calls recorded)"

    # Tag each snapshot with the URL the agent was on when it took the
    # snapshot, by walking the trace and tracking the last-seen goto URL.
    snapshots: list[tuple[str, str, str, str]] = []  # (tool, label, url, output)
    current_url = pred.get("start_url") or ""
    for entry in trace:
        tool = entry.get("tool", "")
        if tool == "goto":
            url = (entry.get("args") or {}).get("url") or ""
            if url:
                current_url = url
            continue
        output = entry.get("output")
        if tool in PAGE_SNAPSHOT_TOOLS and output:
            label = _summarize_args(tool, entry.get("args"))
            snapshots.append((tool, label, current_url, output))

    snapshots = snapshots[-N_SNAPSHOTS_TO_SHOW:]
    if snapshots:
        snapshot_sections = []
        for i, (tool, label, url, output) in enumerate(snapshots, 1):
            header = f"[snapshot {i}] tool={tool}"
            if label:
                header += f" args={label}"
            header += f" page={url}" if url else ""
            body = output[:SNAPSHOT_MAX_CHARS]
            if len(output) > SNAPSHOT_MAX_CHARS:
                body += "...[truncated]"
            snapshot_sections.append(f"{header}\n{body}")
        snapshots_block = "\n\n".join(snapshot_sections)
    else:
        snapshots_block = "(no page-reading tool outputs recorded — the agent may have answered without inspecting any page)"

    reference = pred.get("reference")
    reference_block = (
        f"\nREFERENCE ANSWER (one valid outcome among possibly many; hint, not strict match):\n  {reference}\n"
        if reference
        else ""
    )

    return (
        f"TASK ({pred.get('web_name', '?')}): {pred.get('task', '')}\n"
        f"START URL: {pred.get('start_url', '')}\n\n"
        f"VISITED URLS (in order):\n{urls_block}\n\n"
        f"PAGE SNAPSHOTS (last {len(snapshots)} page-reading tool outputs, in order):\n{snapshots_block}\n\n"
        f"AGENT ANSWER:\n{pred.get('prediction') or '(empty)'}\n"
        f"{reference_block}"
    )


def _parse_judge_response(text: str) -> tuple[str, str]:
    """Pull VERDICT and REASONING out of the judge's structured reply. On
    malformed output we return INVALID + the raw text so it surfaces in the
    report instead of silently being scored as NO."""
    verdict = "INVALID"
    reasoning = text.strip()
    for line in text.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("VERDICT:"):
            value = stripped.split(":", 1)[1].strip().upper()
            if value.startswith("YES"):
                verdict = "YES"
            elif value.startswith("NO"):
                verdict = "NO"
        elif upper.startswith("REASONING:"):
            reasoning = stripped.split(":", 1)[1].strip()
    return verdict, reasoning


def _judge_provider_for(model: str) -> str:
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith(("gemini-", "models/gemini-")):
        return "gemini"
    raise ValueError(
        f"unsupported judge model: {model!r}. "
        "Supported prefixes: 'claude-*' (Anthropic), 'gemini-*' (Google). "
        "Add a new branch to _judge_provider_for / _make_judge_client / _call_judge to extend."
    )


def _make_judge_client(provider: str) -> Any:
    if provider == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set for claude-* judge.")
        from anthropic import Anthropic  # type: ignore[import-not-found]

        return Anthropic()
    if provider == "gemini":
        if not (os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")):
            raise RuntimeError("GOOGLE_API_KEY (or GEMINI_API_KEY) is not set for gemini-* judge.")
        from google import genai  # type: ignore[import-not-found]

        return genai.Client()
    raise ValueError(f"unknown judge provider: {provider}")


def _call_judge(provider: str, client: Any, model: str, system: str, user_prompt: str) -> str:
    if provider == "anthropic":
        resp = client.messages.create(
            model=model,
            max_tokens=256,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        )
    if provider == "gemini":
        # google-genai exposes system instructions via GenerateContentConfig.
        # Pin a small thinking budget: Gemini 3.x Pro can stall on a quick
        # verdict when an unbounded budget lets it self-deliberate.
        from google.genai import types  # type: ignore[import-not-found]

        resp = client.models.generate_content(
            model=model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=1024,
                thinking_config=types.ThinkingConfig(thinking_budget=512),
            ),
        )
        return resp.text or ""
    raise ValueError(f"unknown judge provider: {provider}")


def _judge_one(
    provider: str,
    client: Any,
    model: str,
    pred: dict[str, Any],
) -> dict[str, Any]:
    try:
        text = _call_judge(provider, client, model, JUDGE_SYSTEM_PROMPT, _format_prompt(pred))
        verdict, reasoning = _parse_judge_response(text)
    except Exception as e:
        verdict = "INVALID"
        reasoning = f"judge call failed: {type(e).__name__}: {e}"
    return {
        "id": pred.get("id"),
        "web_name": pred.get("web_name"),
        "verdict": verdict,
        "reasoning": reasoning,
    }


def grade_predictions(
    predictions_path: Path,
    *,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    workers: int = 4,
    variant: str = "text-only",
) -> dict[str, Any]:
    """Grade a predictions.jsonl with the LLM judge and return the scores
    dict. `variant` is recorded in the result so callers can distinguish
    suite-specific protocols (e.g. webvoyager's `text-only` vs webbench's
    `webbench-text-only`)."""
    provider = _judge_provider_for(judge_model)
    client = _make_judge_client(provider)

    preds: list[dict[str, Any]] = []
    with predictions_path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                preds.append(json.loads(line))

    verdicts: list[dict[str, Any]] = [{} for _ in preds]

    if workers <= 1:
        for i, p in enumerate(preds):
            verdicts[i] = _judge_one(provider, client, judge_model, p)
            print(
                f"  [{i + 1}/{len(preds)}] {verdicts[i]['verdict']} {verdicts[i]['id']}",
                file=sys.stderr,
            )
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {
                ex.submit(_judge_one, provider, client, judge_model, p): i
                for i, p in enumerate(preds)
            }
            for done, fut in enumerate(concurrent.futures.as_completed(futures), 1):
                i = futures[fut]
                verdicts[i] = fut.result()
                print(
                    f"  [{done}/{len(preds)}] {verdicts[i]['verdict']} {verdicts[i]['id']}",
                    file=sys.stderr,
                )

    yes = sum(1 for v in verdicts if v["verdict"] == "YES")
    no = sum(1 for v in verdicts if v["verdict"] == "NO")
    invalid = sum(1 for v in verdicts if v["verdict"] == "INVALID")
    n = len(verdicts)

    by_site: dict[str, list[str]] = {}
    for v in verdicts:
        by_site.setdefault(v.get("web_name") or "?", []).append(v["verdict"])

    durations = [p.get("duration_s", 0.0) for p in preds]
    timeouts = sum(1 for p in preds if p.get("timed_out"))
    answered = sum(1 for p in preds if (p.get("prediction") or "").strip())

    per_task: list[dict[str, Any]] = []
    for p, v in zip(preds, verdicts, strict=True):
        per_task.append(
            {
                "id": p.get("id"),
                "web_name": p.get("web_name"),
                "task": p.get("task"),
                "verdict": v["verdict"],
                "reasoning": v["reasoning"],
                "duration_s": p.get("duration_s"),
                "timed_out": bool(p.get("timed_out")),
                "n_goto": sum(1 for entry in (p.get("trace") or []) if entry.get("tool") == "goto"),
            }
        )

    return {
        "n_tasks": n,
        "n_answered": answered,
        "timeouts": timeouts,
        "accuracy": yes / n if n else 0.0,
        "yes": yes,
        "no": no,
        "invalid": invalid,
        "judge_model": judge_model,
        "judge_provider": provider,
        "variant": variant,
        "by_site": {
            site: {
                "n": len(vs),
                "accuracy": sum(1 for v in vs if v == "YES") / len(vs) if vs else 0.0,
                "yes": sum(1 for v in vs if v == "YES"),
                "no": sum(1 for v in vs if v == "NO"),
                "invalid": sum(1 for v in vs if v == "INVALID"),
            }
            for site, vs in sorted(by_site.items())
        },
        "avg_duration_s": mean(durations),
        "per_task": per_task,
    }
