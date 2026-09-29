"""The element table both browsers produce and both deciders read.

Upstream keeps its table small for free: it captures only elements whose centre
lies inside a 1120x780 viewport. Lightpanda has no layout to ask -- its
getBoundingClientRect returns a flat 5.0 box at a node-ordinal position -- so
the Chrome adapter clips by viewport and the Lightpanda adapter cannot. Both
then pass through the same bound(): dedupe on (role, label), cap, truncate. The
cap is what keeps a choice question under the API's 255-option ceiling on
link-dense pages.
"""

import hashlib
import json
import os
from dataclasses import dataclass, field

from . import prompts


class Stale(RuntimeError):
    """The page moved out from under a decision. Observe again."""


# The API caps a choice question at 255 options; the Vercel gateway gives out
# well before that, so the demo default is lower than the ceiling.
MAX_OFFERED = int(os.environ.get("ULTRAFAST_MAX_OFFERED", 120))
MAX_COLLECTED = 5000
MAX_LABEL_BYTES = 120
TEXT_BYTES = 6000
RECENT_ACTIONS = 10


def clip(text, limit=MAX_LABEL_BYTES):
    if text is None:
        return None
    encoded = text.strip().encode()
    if len(encoded) <= limit:
        return encoded.decode()
    return encoded[:limit].decode(errors="ignore")


@dataclass
class Element:
    """One observed control. `node` is browser-owned, never model-generated."""

    node: object
    role: str
    label: str
    value: str | None = None
    checked: bool | None = None
    ops: list[str] = field(default_factory=list)
    options: list[tuple[str, str]] = field(default_factory=list)
    index: str = ""


@dataclass
class State:
    url: str
    title: str
    text: str
    elements: list[Element]
    omitted: int = 0
    can_scroll_down: bool = False
    can_scroll_up: bool = False


def bound(candidates, cap=None):
    """Dedupe on (role, label), cap, truncate. Returns (elements, omitted).

    Unnamed controls are exempt from the dedupe: a Hacker News item page carries
    one nameless upvote arrow per row and one per row is the whole point of them.
    """
    cap = MAX_OFFERED if cap is None else cap
    kept, seen = [], set()
    for candidate in candidates[:MAX_COLLECTED]:
        candidate.label = clip(candidate.label) or ""
        candidate.value = clip(candidate.value)
        candidate.options = [(v, clip(label)) for v, label in candidate.options]
        if candidate.label:
            key = (candidate.role, candidate.label)
            if key in seen:
                continue
            seen.add(key)
        kept.append(candidate)
    omitted = len(candidates) - min(len(kept), cap)
    kept = kept[:cap]
    for position, element in enumerate(kept, 1):
        element.index = str(position)
    return kept, max(omitted, 0)


def fingerprint(state):
    """Semantic identity of an observation. Deliberately not node ids, not
    geometry, not page text: those move under animations and re-renders without
    changing what a decision means."""
    content = [
        state.url,
        state.title,
        [
            [e.role, e.label, e.value, e.checked, e.ops, e.options]
            for e in state.elements
        ],
    ]
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def state_json(state, step, history, text_bytes=TEXT_BYTES):
    """The observation the decider sees. Absent values are omitted rather than
    sent as null -- every byte here is a billed input token."""
    elements = []
    for element in state.elements:
        entry = {"i": element.index, "role": element.role, "label": element.label}
        if element.value:
            entry["value"] = element.value
        if element.checked is not None:
            entry["checked"] = element.checked
        entry["ops"] = element.ops
        if element.options:
            entry["options"] = [label for _value, label in element.options]
        elements.append(entry)
    observation = {
        "step": step,
        "page": {"url": state.url, "title": state.title, "text": state.text[:text_bytes]},
        "elements": elements,
    }
    if state.omitted:
        observation["elements_omitted"] = state.omitted
    if history:
        observation["recent_actions"] = [
            {k: h[k] for k in ("op", "target", "text", "ok", "page_changed")
             if h.get(k) is not None}
            for h in history[-RECENT_ACTIONS:]
        ]
    return observation


def describe(element, option=None):
    """One line per offered target, in the shape the criteria want."""
    line = f"[{element.index}] {element.label or element.role} ({element.role})"
    if option is not None:
        return f"{line} -> {option}"
    if element.value:
        line += f" = {element.value}"
    if element.checked is True:
        line += " [checked]"
    elif element.checked is False:
        line += " [unchecked]"
    return line


TARGET_QUESTION = {
    "CLICK": "click_target",
    "TYPE_TEXT": "type_text_target",
    "SELECT": "select_target",
}


def action_space(state, can_type):
    """Returns (operations, targets). `targets` maps an operation to
    {option id -> (element, option value)}; the option id is what the decider
    chooses and what resolves back to a real node."""
    targets = {}
    for element in state.elements:
        for op in element.ops:
            if op == "TYPE_TEXT" and not can_type:
                continue
            group = targets.setdefault(op, {})
            if op == "SELECT":
                for ordinal, (value, label) in enumerate(element.options, 1):
                    group[f"{element.index}:{ordinal}"] = (element, value, label)
            else:
                group[element.index] = (element, None, None)
    operations = {op: prompts.OPERATIONS[op] for op in TARGET_QUESTION if op in targets}
    if state.can_scroll_down:
        operations["SCROLL_DOWN"] = prompts.OPERATIONS["SCROLL_DOWN"]
    if state.can_scroll_up:
        operations["SCROLL_UP"] = prompts.OPERATIONS["SCROLL_UP"]
    operations["WAIT"] = prompts.OPERATIONS["WAIT"]
    operations["DONE"] = prompts.OPERATIONS["DONE"]
    operations["BLOCKED"] = prompts.OPERATIONS["BLOCKED"]
    return operations, targets


def criteria(operation, group, described=True):
    """The offered set for one target head.

    The keys are what the decider chooses among and what `parseTarget` resolves,
    so they are identical whichever shape is asked for. The values are a second
    copy of the element table, which `state.elements` already carries under the
    same indices; `described=False` drops that copy.
    """
    if not described:
        return dict.fromkeys(group)
    return {
        option_id: describe(element, label)
        for option_id, (element, _value, label) in group.items()
    }
