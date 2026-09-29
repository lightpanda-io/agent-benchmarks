"""The control: one chat turn per step, over the same table and the same rules.

Unlike benchmarks/jev-vs-chat, this arm does not carry a growing conversation.
It is handed exactly the observation Jev is handed -- same element table, same
recent_actions window, same NEXT_ACTION and TARGET text -- so a difference
between the two arms is a difference between the deciders, not between what
they were shown.
"""

import json
import os
import time

from . import net, prompts, table, text
from .policy_jev import Decision

# USD per million tokens, list price.
PRICES = {
    "google/gemini-3.8-flash": (0.30, 2.50),
    "anthropic/claude-sonnet-5": (3.00, 15.00),
    "openai/gpt-5.5": (1.25, 10.00),
    "inception/mercury-2.5": (0.25, 1.00),
}


class Chat:
    name = "chat"

    def __init__(self):
        self.key = os.environ.get("CHAT_API_KEY") or os.environ.get("AI_GATEWAY_API_KEY")
        if not self.key:
            raise SystemExit("--policy chat needs CHAT_API_KEY, or AI_GATEWAY_API_KEY to reach "
                             "the chat side through the same gateway Jev is on")
        self.base = os.environ.get("CHAT_BASE_URL", "https://ai-gateway.vercel.sh/v1").rstrip("/")
        self.model = os.environ.get("CHAT_MODEL", "google/gemini-3.8-flash")
        self.can_type = bool(text.text_key())

    def choose(self, state, goal, step, history):
        try:
            return self._choose(state, goal, step, history)
        except ValueError:
            # Jev is never re-asked: a calibrated decoder returns the same answer
            # for the same state. A chat model does not, so one malformed reply
            # gets one more turn rather than ending the run.
            return self._choose(state, goal, step, history)

    def _choose(self, state, goal, step, history):
        operations, targets = table.action_space(state, self.can_type)
        payload = {
            "goal": goal,
            "observation": table.state_json(state, step, history),
            "operations": operations,
            "targets": {op: table.criteria(op, group) for op, group in targets.items()},
        }
        started = time.perf_counter()
        result = net.post_json(f"{self.base}/chat/completions", self.key, {
            "model": self.model,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": prompts.CHAT_SYSTEM},
                {"role": "user", "content": json.dumps(payload)},
            ],
        })
        latency = round((time.perf_counter() - started) * 1000)
        try:
            answer = json.loads(result["choices"][0]["message"]["content"])
            operation = answer["operation"]
        except (ValueError, KeyError, TypeError):
            raise ValueError("chat decider returned no valid JSON decision") from None
        if operation not in operations:
            raise ValueError(f"chat decider chose an unoffered operation {operation!r}")
        decision = Decision(
            operation=operation,
            latency_ms=latency,
            usage=result.get("usage", {}),
            model=result.get("model", self.model),
        )
        if operation in targets:
            chosen = str(answer.get("target"))
            if chosen not in targets[operation]:
                raise ValueError(f"chat decider chose an unoffered target {chosen!r}")
            element, value, label = targets[operation][chosen]
            decision.target, decision.element = chosen, element
            decision.option, decision.option_label = value, label
        return decision

    def cost(self, input_tokens, output_tokens):
        price_in, price_out = PRICES.get(self.model, (0.0, 0.0))
        return input_tokens / 1e6 * price_in + output_tokens / 1e6 * price_out
