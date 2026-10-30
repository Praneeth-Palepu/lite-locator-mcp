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

from .snapshot import CONTAINERS, INTERACTIVE, card_of, parse_tree

Snap = namedtuple("Snap", "url title snapshot")
STATES = ("checked", "disabled", "expanded", "selected", "pressed")
GROUPS = {"dialog", "alertdialog", "alert", "menu", "listbox", "tooltip", "status"}
MAX_LINES = 25
REPLACED = 0.6  # more than this share of the tree changed: treat as a new view (SPA route change)


Item = namedtuple("Item", "key line path node")
CARD_MIN = 3  # card context only where a role repeats this often (product grids, result lists)


def _items(snapshot):
    """Meaningful nodes in page order. key = role + name (to pair a changed element), line = what the LLM reads
    (role "name": text [state]), path = position in the tree (stable while text changes: used for the noise filter)."""
    out = []
    for n in parse_tree(snapshot).walk():
        if n.role == "document" or (n.role in CONTAINERS and not n.name and n.role not in GROUPS):
            continue
        label = n.name or (n.text if n.role != "text" else "")
        text = n.text if n.text and n.text != label else ""
        if not (label or text or n.role in INTERACTIVE or n.role in GROUPS):
            continue
        st = [s for s in STATES if n.attrs.get(s) not in (None, False, "false")]
        line = n.role + (f' "{label[:80]}"' if label and n.role != "text" else "") + (f": {text[:100]}" if text else "")
        line += "".join(f" [{s}]" for s in st)
        group = next((a for a in _ancestors(n) if a.role in GROUPS), None)
        if group is not None and group is not n:
            line += f'  (in {group.role}{" " + repr(group.name) if group.name else ""})'
        out.append(Item((n.role, label[:80]), line, _path(n), n))
    return out


def _path(n):
    p, a = [], n
    while a.parent is not None:
        p.append((a.role, a.parent.children.index(a)))
        a = a.parent
    return tuple(reversed(p))


def _with_card(item, items):
    """Add the card title when the element's role repeats (which "Remove" button? the Backpack's)."""
    n = item.node
    if n.role not in INTERACTIVE:
        return item.line
    members = [x.node for x in items if x.node.role == n.role]
    if len(members) < CARD_MIN:
        return item.line
    card = card_of(n, members) or _heading_before(n, members)
    return item.line + (f"  (card: {card[:60]})" if card else "")


def _heading_before(n, members):
    """Flattened lists (Playwright's aria_snapshot drops unnamed divs): the heading just before the element, with no
    look-alike in between."""
    sibs = n.parent.children if n.parent else []
    for s in reversed(sibs[:sibs.index(n)] if n in sibs else []):
        if s in members:
            return ""
        if s.role == "heading" and s.label():
            return s.label()
    return ""


def _ancestors(n):
    a = n.parent
    while a is not None:
        yield a
        a = a.parent


def summary(snap, headings=5):
    """A new page in a few lines: title, main headings and what can be done there."""
    items = _items(snap.snapshot)
    heads = [x.key[1] for x in items if x.key[0] == "heading" and x.key[1]][:headings]
    counts = Counter(x.key[0] for x in items if x.key[0] in INTERACTIVE)
    plural = lambda r, n: r if n == 1 else r + ("es" if r.endswith(("x", "s")) else "s")
    can = ", ".join(f"{n} {plural(r, n)}" for r, n in counts.most_common(6))
    out = [f"Page: {snap.title or '(no title)'}  {snap.url}"]
    if heads:
        out.append("Headings: " + " | ".join(heads))
    if can:
        out.append("Has: " + can)
    return out


def noise(s1, s2):
    """Tree positions that changed between two snapshots taken without any action (tickers, carousels, timers):
    changes there after an action are not caused by it."""
    a, b = _items(s1), _items(s2)
    sm = SequenceMatcher(None, [x.line for x in a], [x.line for x in b], autojunk=False)
    return {x.path for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal" for x in a[i1:i2] + b[j1:j2]}


def changes(before, after, ignore=frozenset()):
    """Added (+), removed (-) and changed (~) elements between two snapshots of the same page, in page order.
    ignore: tree positions from noise() to leave out."""
    a, b = _items(before), _items(after)
    sm = SequenceMatcher(None, [x.line for x in a], [x.line for x in b], autojunk=False)
    lines, changed = [], 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        old = [x for x in a[i1:i2] if x.path not in ignore]
        new = [x for x in b[j1:j2] if x.path not in ignore]
        changed += max(len(old), len(new))
        olds = {x.key: x for x in old}
        paired = set()
        for x in new:
            if x.key in olds and x.key[1] and x.key not in paired:  # same element, new text / state
                lines.append(f"~ {olds[x.key].line}  ->  {_with_card(x, b)}")
                paired.add(x.key)
            else:
                lines.append(f"+ {_with_card(x, b)}")
        lines += [f"- {_with_card(x, a)}" for x in old if x.key not in paired]
    return lines, changed / max(1, len(a), len(b))


NO_CHANGE = ("No change detected within {secs:g} s. If this action submits, pays or deletes something, do not repeat "
             "it: check the page with browser_snapshot first.")


def outcome(before, after, ignore=frozenset(), waited=0.6, extra=()):
    """The result of one action as a few lines of text. extra: lines about new tabs / dialogs, shown first."""
    head = list(extra)
    nav = before is None or before.url.split("#")[0] != after.url.split("#")[0]
    if nav:
        return "\n".join(head + ["Navigated to a new page."] + summary(after) +
                         ["Use browser_find_element to locate elements on it."])
    lines, share = changes(before.snapshot, after.snapshot, ignore)
    if not lines:
        return "\n".join(head + [NO_CHANGE.format(secs=round(waited, 1))])
    if share > REPLACED:
        return "\n".join(head + ["Same URL, but most of the page changed (new view)."] + summary(after) +
                         ["Use browser_find_element to locate elements on it."])
    more = f"\n(+{len(lines) - MAX_LINES} more changes)" if len(lines) > MAX_LINES else ""
    return "\n".join(head + ["Same page. Changes:"] + lines[:MAX_LINES]) + more


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
