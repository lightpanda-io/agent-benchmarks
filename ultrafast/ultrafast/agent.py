"""The loop: observe, decide, act. The only thing on the clock.

Timing starts at the first decision after the initial observation and ends at
the accepted DONE, which is upstream's definition, so the numbers sit next to
theirs. Browser launch, the initial navigation and the independent post-run
check are outside it.
"""

import time

from . import prompts, table, text


class Agent:
    def __init__(self, browser, policy, goal, verbose=False, record=False):
        self.browser = browser
        self.policy = policy
        self.goal = goal
        self.verbose = verbose
        self.record = record
        self.trace = []
        self.history = []
        self.decisions = []
        self.text_calls = []
        self.observations = []
        self.status = "ready"
        self.error = None
        self.elapsed_ms = 0
        self._just_observed = True
        self.state = browser.observe()
        self.observations.append(len(self.state.elements))
        self._pending_text = None

    def _reobserve(self):
        self.state = self.browser.observe()
        self.observations.append(len(self.state.elements))
        self._just_observed = True

    def _fresh(self):
        """Whether the action space still means what it meant when we decided.

        Leaves `self.state` current either way; the answer is whether it moved.
        Compares the table's fingerprint rather than every input value in the
        document: a page that keeps adding inputs after load moves a value
        marker without changing any choice the decider could make, and that
        cost a whole discarded decision on every run.
        """
        if self._just_observed:
            return True
        before = table.fingerprint(self.state)
        self._reobserve()
        return table.fingerprint(self.state) == before

    def _target_fresh(self, decision):
        """Whether the element a decision names still means what it meant.

        Scoped to the target on purpose. A page that is still loading rewrites
        the table around a choice without invalidating the choice itself, and
        checking the whole page instead threw away a good decision on every
        run. Same node, same role, same label, same operations: clicking it
        does what the decider asked for.
        """
        if self._just_observed:
            return True
        chosen = decision.element
        self._reobserve()
        for element in self.state.elements:
            if element.node == chosen.node:
                return (element.role, element.label, element.ops) == (
                    chosen.role, chosen.label, chosen.ops)
        return False

    def run(self):
        started = time.perf_counter()
        while self.status == "ready":
            try:
                self._step(started)
            except table.Stale:
                self._reobserve()
            except (ValueError, RuntimeError) as exc:
                self.status = "error"
                self.error = f"{type(exc).__name__}: {exc}"
            self.elapsed_ms = round((time.perf_counter() - started) * 1000)
            yield self
        self.elapsed_ms = round((time.perf_counter() - started) * 1000)

    def _step(self, started):
        if len(self.decisions) >= prompts.MAX_STEPS * 2:
            self.status = "budget"
            return
        # A page that moved is simply re-read before we decide on it.
        self._fresh()
        fingerprint = table.fingerprint(self.state)
        self._just_observed = False
        decision = self.policy.choose(self.state, self.goal, len(self.history) + 1, self.history)
        self.decisions.append({
            "op": decision.operation,
            "target": decision.target,
            "latency_ms": decision.latency_ms,
            "confidence": decision.confidence,
            "usage": decision.usage,
            "elements": len(self.state.elements),
            "omitted": self.state.omitted,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        })
        if self.record:
            self.trace.append({
                "step": len(self.history) + 1,
                "at_ms": round((time.perf_counter() - started) * 1000) - decision.latency_ms,
                "decided_ms": round((time.perf_counter() - started) * 1000),
                "latency_ms": decision.latency_ms,
                "url": self.state.url,
                "title": self.state.title,
                "elements": [
                    {"i": e.index, "role": e.role, "label": e.label,
                     "value": e.value, "checked": e.checked, "ops": e.ops}
                    for e in self.state.elements[:40]
                ],
                "omitted": self.state.omitted,
                "offered": len(self.state.elements),
                "op": decision.operation,
                "target": decision.target,
                "target_label": _label(decision),
                "confidence": decision.confidence,
                "op_probabilities": getattr(decision, "op_probabilities", {}),
                "target_probabilities": dict(sorted(
                    getattr(decision, "target_probabilities", {}).items(),
                    key=lambda kv: -kv[1])[:6]),
                "tokens": decision.usage.get("input_tokens",
                                             decision.usage.get("prompt_tokens", 0)),
            })
        if self.verbose:
            print(f"  {decision.latency_ms:>5}ms  {decision.operation:<11}"
                  f"{_label(decision)}", flush=True)

        if decision.operation in ("DONE", "BLOCKED"):
            if not self._fresh():
                raise table.Stale("page changed since the decision")
            self.status = "done" if decision.operation == "DONE" else "blocked"
            return

        if len(self.history) >= prompts.MAX_STEPS:
            self.status = "budget"
            return

        typed, helper = None, None
        if decision.operation == "TYPE_TEXT":
            payload = text.context(self.goal, decision.element, self.state, self.history)
            if self._pending_text and self._pending_text[0] == payload:
                _, typed, helper = self._pending_text
            else:
                typed, helper = text.field_text(payload)
                self._pending_text = (payload, typed, helper)
                self.text_calls.append({**helper, "field": decision.element.label, "value": typed})

        if not self._target_fresh(decision):
            raise table.Stale("the chosen element changed before execution")
        self.browser.act(decision.operation, decision.element, decision.option, typed)
        self._pending_text = None

        # Log the execution before observing: a stale post-action read must not
        # erase an action that did happen.
        if self.record and self.trace:
            self.trace[-1]["text"] = typed
            self.trace[-1]["text_ms"] = helper["latency_ms"] if helper else 0
            self.trace[-1]["acted_ms"] = round((time.perf_counter() - started) * 1000)
        self.history.append({
            "step": len(self.history) + 1,
            "op": decision.operation,
            "target": _label(decision) or decision.operation,
            "text": typed,
            "ok": True,
            "latency_ms": decision.latency_ms,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        })
        self._reobserve()
        self.history[-1]["page_changed"] = table.fingerprint(self.state) != fingerprint

        recent = self.history[-3:]
        if len(recent) == 3 and all(
                h["page_changed"] is False and h["op"] != "WAIT" for h in recent):
            self.status = "blocked"

    def totals(self):
        latencies = sorted(d["latency_ms"] for d in self.decisions)
        counts = sorted(self.observations)
        usage_in = sum(d["usage"].get("input_tokens", d["usage"].get("prompt_tokens", 0))
                       for d in self.decisions)
        usage_out = sum(d["usage"].get("output_tokens", d["usage"].get("completion_tokens", 0))
                        for d in self.decisions)
        return {
            "ms": self.elapsed_ms,
            "status": self.status,
            "error": self.error,
            "decisions": len(self.decisions),
            "actions": len(self.history),
            "decide_ms_median": _median(latencies),
            "browser_calls": self.browser.calls,
            "browser_ms": round(self.browser.call_ms),
            "elements_median": _median(counts),
            "elements_max": max(counts, default=0),
            "input_tokens": usage_in,
            "output_tokens": usage_out,
            "text_calls": len(self.text_calls),
            "text_ms": sum(c["latency_ms"] for c in self.text_calls),
        }


def _label(decision):
    """What was actually done, including which option was picked -- the decider
    reads this back as recent_actions, so 'Stay category' alone would let it
    re-select a dropdown it had already set."""
    if decision.element is None:
        return ""
    return table.describe(decision.element, decision.option_label)


def _median(values):
    if not values:
        return 0
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return round((values[middle - 1] + values[middle]) / 2)
