"""Jev: one request answers which operation and which target at once.

The request carries the operation question plus one speculative target head per
operation that has candidates. Only the head the chosen operation names is read
and validated -- an unused head cost input tokens but no round trip, and cannot
reach the browser.
"""

import math
import os
import time
from dataclasses import dataclass, field, replace

from . import net, prompts, table, text

CHANNELS = (
    ("TYPESAFE_API_KEY", "https://api.typesafe.ai", "jev-latest"),
    ("AI_GATEWAY_API_KEY", "https://ai-gateway.vercel.sh/typesafe", "typesafe-ai/jev"),
)
PRICE_IN = 0.042  # USD per million input tokens, list. Output is free.


def detect():
    """A TypeSafe key is the more specific signal: a gateway key also serves a
    hundred chat models."""
    for env, base, model in CHANNELS:
        key = os.environ.get(env)
        if key:
            return key, os.environ.get("JEV_BASE_URL", base), os.environ.get("JEV_MODEL", model)
    raise SystemExit("--policy jev needs TYPESAFE_API_KEY, or AI_GATEWAY_API_KEY "
                     "to reach Jev through the Vercel gateway")


@dataclass
class Decision:
    operation: str
    element: object = None
    option: str | None = None
    option_label: str | None = None
    target: str | None = None
    confidence: float | None = None
    latency_ms: int = 0
    usage: dict = field(default_factory=dict)
    model: str = ""
    op_probabilities: dict = field(default_factory=dict)
    target_probabilities: dict = field(default_factory=dict)


def validate_choice(answer, offered):
    """Replicated from zenai's typesafe.validateChoice. An invalid decision is
    never retried: a calibrated decoder returns the same answer for the same
    state."""
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in offered
            and set(probabilities) == set(offered)
            and all(isinstance(n, int | float) and math.isfinite(n) and 0 <= n <= 1
                    for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("invalid Jev response; no action executed")
    return answer


#: How much of the request to send. The keys of every target head are identical
#: across shapes; only the duplicated element descriptions and the page text move.
SHAPES = {
    "described": {"described": True, "text_bytes": table.TEXT_BYTES},
    "bare": {"described": False, "text_bytes": table.TEXT_BYTES},
    "bare-trim": {"described": False, "text_bytes": 2000},
}


class Jev:
    name = "jev"

    def __init__(self, shape="described"):
        self.key, self.base, self.model = detect()
        self.can_type = bool(text.text_key())
        if shape not in SHAPES:
            raise SystemExit(f"unknown --request-shape {shape!r}; pick one of {', '.join(SHAPES)}")
        self.shape = shape

    def build(self, state, goal, step, history, scale=1.0):
        """The request at a given fraction of full size.

        Shrinking drops the tail of the element table and trims the page text.
        It never reorders or reweights what survives, so a degraded request is a
        smaller version of the same question rather than a different one.
        """
        if scale < 1.0:
            kept = max(12, int(len(state.elements) * scale))
            state = replace(
                state,
                elements=state.elements[:kept],
                omitted=state.omitted + (len(state.elements) - kept),
                text=state.text[: max(800, int(SHAPES[self.shape]["text_bytes"] * scale))])
        shape = SHAPES[self.shape]
        operations, targets = table.action_space(state, self.can_type)
        observation = table.state_json(state, step, history, text_bytes=shape["text_bytes"])
        # The goal rides in the instructions, not in the state: the questions run
        # independently, so each one states its own premise.
        questions = {"operation": {
            "type": "choice",
            "instructions": {"goal": goal, "rules": prompts.NEXT_ACTION},
            "criteria": operations,
        }}
        for operation, group in targets.items():
            questions[table.TARGET_QUESTION[operation]] = {
                "type": "choice",
                "instructions": {"goal": goal, "operation": operation,
                                 "rules": [prompts.NEXT_ACTION, prompts.TARGET]},
                "criteria": table.criteria(operation, group, described=shape["described"]),
            }
        body = {"model": self.model, "state": observation, "questions": questions}
        return body, operations, targets

    def choose(self, state, goal, step, history):
        body, operations, targets = self.build(state, goal, step, history)
        heads = {"operations": operations, "targets": targets}

        def shrink(attempt):
            # 70%, 49%, 34% ... of the table per successive rejection. A 503 is a
            # size complaint, so resending the same bytes mostly fails the same way.
            scale = 0.7 ** (attempt + 1)
            smaller, ops, tgts = self.build(state, goal, step, history, scale)
            heads["operations"], heads["targets"] = ops, tgts
            return smaller, scale

        started = time.perf_counter()
        result = net.post_json(f"{self.base}/v1/systemone", self.key, body, shrink=shrink)
        operations, targets = heads["operations"], heads["targets"]
        latency = round((time.perf_counter() - started) * 1000)

        answer = validate_choice(result["answers"].get("operation", {}), operations)
        operation = answer["choice"]
        decision = Decision(
            operation=operation,
            confidence=answer.get("confidence"),
            latency_ms=latency,
            usage=result.get("usage", {}),
            model=result.get("model", self.model),
            op_probabilities=answer.get("probabilities", {}),
        )
        if operation in targets:
            group = targets[operation]
            head = validate_choice(
                result["answers"].get(table.TARGET_QUESTION[operation], {}), group)
            chosen = head["choice"]
            decision.target_probabilities = head.get("probabilities", {})
            element, value, label = group[chosen]
            decision.target, decision.element = chosen, element
            decision.option, decision.option_label = value, label
        return decision

    @staticmethod
    def cost(input_tokens, _output_tokens):
        return input_tokens / 1e6 * PRICE_IN
