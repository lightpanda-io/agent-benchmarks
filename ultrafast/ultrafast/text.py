"""The one chat call in the loop: the value for a TYPE_TEXT field.

Shared by both deciders, so a jev-vs-chat difference is never a difference in
who wrote the text. The executor never lifts a quoted literal out of the goal.
"""

import json
import os
import time

from . import net, prompts


def text_key():
    return os.environ.get("TEXT_MODEL_API_KEY") or os.environ.get("AI_GATEWAY_API_KEY")


def context(goal, element, state, history):
    return {
        "goal": goal,
        "field": {"label": element.label, "role": element.role, "value": element.value},
        "page": {"title": state.title, "text": state.text[:6000]},
        "recent_actions": [{k: h.get(k) for k in ("op", "text")} for h in history[-6:]],
    }


def field_text(payload):
    key = text_key()
    if not key:
        raise ValueError("TYPE_TEXT needs TEXT_MODEL_API_KEY; the executor guesses nothing")
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://ai-gateway.vercel.sh/v1").rstrip("/")
    model = os.environ.get("TEXT_MODEL", "inception/mercury-2.5")
    started = time.perf_counter()
    result = net.post_json(base + "/chat/completions", key, {
        "model": model,
        "max_tokens": 1024,
        "response_format": {"type": "json_object"},
        "reasoning": {"enabled": False},
        "messages": [
            {"role": "system", "content": prompts.TEXT_VALUE},
            {"role": "user", "content": json.dumps(payload)},
        ],
    })
    try:
        output = json.loads(result["choices"][0]["message"]["content"])
        value = output["text"]
        if (set(output) != {"text"} or not isinstance(value, str)
                or not value.strip() or len(value) > 2000):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise ValueError("text helper returned no valid field value; nothing typed") from None
    return value, {
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
    }
