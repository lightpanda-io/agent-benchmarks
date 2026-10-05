"""Lightpanda adapter: stock binary, no Jev code in the browser.

The whole observation is one `tree` call. Lightpanda's semantic tree already
carries what upstream's injected snapshot.js has to compute -- role, accessible
name, current value, checkbox state, select options with the selected one
marked -- so there is nothing to inject and nothing to keep in sync with the
DOM. What it cannot carry is geometry: there is no layout, so there is no
viewport to clip to and no scrolling to offer. The table is bounded by
table.bound() instead.
"""

import re
import time

from lightpanda import Browser as LightpandaBrowser

from . import table

# <indent><id>[ [i]|[i:disabled]][ <role>][ '<name>'][ value='<v>']
#   [ [checked]|[unchecked]][ options=[...]]
LINE = re.compile(r"^( *)(\d+)((?: \[i(?::disabled)?\])?)(.*)$")
OPTION = re.compile(r"'(.*?)'(\*?)(?=,|\]|$)")

EDITABLE = {"textbox", "searchbox", "spinbutton"}
# Lightpanda's own settle after an interaction. Upstream waits up to two
# animation frames, or 200 ms for an editable combobox whose suggestions arrive
# over the network. There are no animation frames here, but the XHR is real.
SETTLE_MS = 50
COMBOBOX_SETTLE_MS = 200

MARKER = """(() => [location.href, document.title,
  [...document.querySelectorAll('input,select,textarea')]
    .map(e => [e.name || e.id, e.value, e.checked])])()"""


def parse_options(text):
    """options=['a','b'*,'c'] -> [('a', False), ('b', True), ...].

    The renderer does not escape option values, so a value is closed by the
    first quote that is followed by an optional '*' and then a comma or the
    closing bracket. A value containing exactly that sequence would still split
    early; nothing in the task set does."""
    return [(m.group(1), bool(m.group(2))) for m in OPTION.finditer(text)]


def parse_tree(rendered):
    """Returns (candidates, text_lines) from one `tree` rendering."""
    candidates, lines = [], []
    for raw in rendered.splitlines():
        match = LINE.match(raw)
        if not match:
            continue
        _indent, node_id, flags, rest = match.groups()
        options = []
        if " options=[" in rest:
            rest, _, listing = rest.partition(" options=[")
            options = parse_options(listing)
        checked = None
        for suffix, value in ((" [checked]", True), (" [unchecked]", False)):
            if rest.endswith(suffix):
                rest, checked = rest[: -len(suffix)], value
        value = None
        if " value='" in rest:
            rest, _, tail = rest.rpartition(" value='")
            value = tail[:-1] if tail.endswith("'") else tail
        rest = rest.strip()
        role, name = "", None
        if rest.startswith("'"):
            name = rest[1:-1] if rest.endswith("'") else rest[1:]
        else:
            role, _, tail = rest.partition(" ")
            tail = tail.strip()
            if tail.startswith("'"):
                name = tail[1:-1] if tail.endswith("'") else tail[1:]

        if not flags:
            if name:
                lines.append(name)
            continue
        if flags.strip() == "[i:disabled]":
            continue
        if role in ("form", "RootWebArea"):
            continue

        ops = []
        if options:
            ops = ["SELECT"]
        elif role in EDITABLE or role == "combobox":
            ops = ["TYPE_TEXT", "CLICK"]
        else:
            ops = ["CLICK"]
        candidates.append(
            table.Element(
                node=int(node_id),
                role=role or "generic",
                label=name or role or "",
                value=value,
                checked=checked,
                ops=ops,
                options=[(v, v) for v, selected in options if not selected],
            )
        )
    return candidates, lines


class Lightpanda:
    name = "lightpanda"

    def __init__(self, url, binary=None, args=()):
        self.calls = 0
        self.call_ms = 0.0
        self._browser = LightpandaBrowser(binary=binary, args=list(args))
        self._page = self._browser.new_session()
        self._settle = 0
        self._timed("goto", url=url)

    def _timed(self, tool, **kwargs):
        started = time.perf_counter()
        result = self._page.call(tool, **kwargs)
        self.call_ms += (time.perf_counter() - started) * 1000
        self.calls += 1
        return result

    def observe(self):
        """tree + one evaluate. The tree does not carry the page URL or a
        freshness token, so Lightpanda pays a second call per observation where
        Chrome's injected snapshot returns both with the table."""
        if self._settle:
            time.sleep(self._settle / 1000)
            self._settle = 0
        rendered = self._timed("tree")
        marker = self._timed("evaluate", script=MARKER)
        url, title, _values = marker
        candidates, lines = parse_tree(rendered)
        elements, omitted = table.bound(candidates)
        return table.State(
            url=url,
            title=title,
            text="\n".join(lines)[: table.TEXT_BYTES],
            elements=elements,
            omitted=omitted,
        )

    def act(self, operation, element, value=None, text=None):
        if operation == "WAIT":
            time.sleep(0.1)
            return
        if operation == "TYPE_TEXT":
            self._timed("fill", backendNodeId=element.node, value=text)
            self._settle = COMBOBOX_SETTLE_MS if element.role == "combobox" else SETTLE_MS
        elif operation == "SELECT":
            self._timed("selectOption", backendNodeId=element.node, value=value)
            self._settle = SETTLE_MS
        else:
            self._timed("click", backendNodeId=element.node)
            self._settle = SETTLE_MS

    def document(self):
        """The whole page as markdown, for the independent checker only. Not
        the observation the decider saw, and not counted: it runs after the
        clock stops, and both arms must be checked against the same evidence."""
        text = self._timed("markdown")
        url, title, _values = self._timed("evaluate", script=MARKER)
        self.calls -= 2
        return table.State(url=url, title=title, text=text, elements=[])

    def close(self):
        try:
            self._page.close()
        finally:
            self._browser.close()
