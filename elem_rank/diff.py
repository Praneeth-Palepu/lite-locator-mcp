"""What an action changed, instead of a full snapshot after every action.

After a click / fill / hover etc. the page either navigates, changes part of its DOM, or stays the same. The LLM
needs that outcome (to verify the step), not two full accessibility trees; where to act next comes from find().

    outcome(before, after)  ->  "Navigated to …" + a short page summary
                                "Same page. Changes:" + only the added / removed / changed elements
                                "No visible change."  (often means the action did not work)

before / after: Snap(url, title, snapshot), where snapshot is accessibility-tree YAML: a Playwright MCP snapshot
(refs are ignored, they change between snapshots) or Playwright's locator("body").aria_snapshot().
"""
from collections import Counter, namedtuple
from difflib import SequenceMatcher

from .snapshot import CONTAINERS, INTERACTIVE, parse_tree

Snap = namedtuple("Snap", "url title snapshot")
STATES = ("checked", "disabled", "expanded", "selected", "pressed")
GROUPS = {"dialog", "alertdialog", "alert", "menu", "listbox", "tooltip", "status"}
MAX_LINES = 25
REPLACED = 0.6  # more than this share of the tree changed: treat as a new view (SPA route change)


def _items(snapshot):
    """(key, line) per meaningful node in page order. key = role + name (to pair a changed element), line = what
    the LLM reads: role "name": text [state]."""
    out = []
    for n in parse_tree(snapshot).walk():
        if n.role == "document" or (n.role in CONTAINERS and not n.name and n.role not in GROUPS):
            continue
        label = n.name or (n.text if n.role != "text" else "")
        text = n.text if n.text and n.text != n.name else ""
        if not (label or text or n.role in INTERACTIVE or n.role in GROUPS):
            continue
        st = [s for s in STATES if n.attrs.get(s) not in (None, False, "false")]
        line = n.role + (f' "{label[:80]}"' if label and n.role != "text" else "") + (f": {text[:100]}" if text else "")
        line += "".join(f" [{s}]" for s in st)
        group = next((a for a in _ancestors(n) if a.role in GROUPS), None)
        if group is not None and group is not n:
            line += f'  (in {group.role}{" " + repr(group.name) if group.name else ""})'
        out.append(((n.role, label[:80]), line))
    return out


def _ancestors(n):
    a = n.parent
    while a is not None:
        yield a
        a = a.parent


def summary(snap, headings=5):
    """A new page in a few lines: title, main headings and what can be done there."""
    items = _items(snap.snapshot)
    heads = [k[1] for k, _ in items if k[0] == "heading" and k[1]][:headings]
    counts = Counter(k[0] for k, _ in items if k[0] in INTERACTIVE)
    plural = lambda r, n: r if n == 1 else r + ("es" if r.endswith(("x", "s")) else "s")
    can = ", ".join(f"{n} {plural(r, n)}" for r, n in counts.most_common(6))
    out = [f"Page: {snap.title or '(no title)'}  {snap.url}"]
    if heads:
        out.append("Headings: " + " | ".join(heads))
    if can:
        out.append("Has: " + can)
    return out


def changes(before, after):
    """Added (+), removed (-) and changed (~) elements between two snapshots of the same page, in page order."""
    a, b = _items(before), _items(after)
    sm = SequenceMatcher(None, [x[1] for x in a], [x[1] for x in b], autojunk=False)
    lines, changed = [], 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        old, new = a[i1:i2], b[j1:j2]
        changed += max(len(old), len(new))
        olds = {k: line for k, line in old}
        paired = set()
        for k, line in new:
            if k in olds and k[1] and k not in paired:  # same element, new text / state
                lines.append(f"~ {olds[k]}  ->  {line}")
                paired.add(k)
            else:
                lines.append(f"+ {line}")
        lines += [f"- {line}" for k, line in old if k not in paired]
    return lines, changed / max(1, len(a), len(b))


def outcome(before, after):
    """The result of one action as a few lines of text."""
    nav = before is None or before.url.split("#")[0] != after.url.split("#")[0]
    if nav:
        return "\n".join(["Navigated to a new page."] + summary(after) +
                         ["Use browser_find_element to locate elements on it."])
    lines, share = changes(before.snapshot, after.snapshot)
    if not lines:
        return "No visible change on the page (the action may not have worked)."
    if share > REPLACED:
        return "\n".join(["Same URL, but most of the page changed (new view)."] + summary(after) +
                         ["Use browser_find_element to locate elements on it."])
    more = f"\n(+{len(lines) - MAX_LINES} more changes)" if len(lines) > MAX_LINES else ""
    return "Same page. Changes:\n" + "\n".join(lines[:MAX_LINES]) + more


def split_snapshot(text):
    """(text without the YAML snapshot block, snapshot YAML or None, url, title) from a Playwright MCP tool result."""
    url = title = ""
    for ln in text.splitlines():
        if "Page URL:" in ln:
            url = ln.split("Page URL:", 1)[1].strip()
        elif "Page Title:" in ln:
            title = ln.split("Page Title:", 1)[1].strip()
    if "```yaml" not in text:
        return text, None, url, title
    head, rest = text.split("```yaml", 1)
    yaml, tail = rest.split("```", 1) if "```" in rest else (rest, "")
    head = head.replace("### Snapshot", "").replace("- Page Snapshot", "").rstrip()
    return (head + "\n" + tail.strip()).strip(), yaml, url, title
