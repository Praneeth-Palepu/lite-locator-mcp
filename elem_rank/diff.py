"""What an action changed, instead of a full snapshot after every action.

After a click / fill / hover etc. the page either navigates, changes part of its DOM, or stays the same. The LLM
needs that outcome (to verify the step), not two full accessibility trees; where to act next comes from find().

    outcome(before, after)  ->  "Navigated to …" + a short page summary
                                "Same page. Changes:" + only the added / removed / changed elements
                                "No visible change."  (often means the action did not work)

before / after: Snap(url, title, snapshot), where snapshot is accessibility-tree YAML: a Playwright MCP snapshot
(refs are ignored, they change between snapshots) or Playwright's locator("body").aria_snapshot().
"""
import json
import re
from collections import Counter, namedtuple
from difflib import SequenceMatcher

from .snapshot import CONTAINERS, INTERACTIVE, card_of, parse_tree

Snap = namedtuple("Snap", "url title snapshot")
STATES = ("checked", "disabled", "expanded", "selected", "pressed")
GROUPS = {"dialog", "alertdialog", "alert", "menu", "listbox", "tooltip", "status"}
MAX_LINES = 25
REPLACED = 0.6  # more than this share of the tree changed: treat as a new view (SPA route change)
REPLACED_MIN = 20  # ... on pages with at least this many elements


Item = namedtuple("Item", "key line path node msg")
MSG_ROLES = {"alert", "alertdialog", "status"}
MSG_MAX = 300  # messages are kept whole (up to this length): a test needs the exact text
SUMMARY_MESSAGES = 6
# Words of feedback a page gives about an action (errors, validation, confirmations). Generic, not site-specific: they
# only decide what is never dropped (truncation, noise filter, page summaries), never what is shown at all.
MESSAGE = re.compile(
    r"\b(errors?|invalid|required|mandatory|must|please|fail(ed|ure|s)?|incorrect|wrong|cannot|can't|unable|"
    r"not (valid|found|allowed|available|match|matching|recognized|recognised|correct)|(do|does|did)(n't| not) match|"
    r"mismatch|already (exists|registered|taken|in use|used)|too (short|long|many|few|large|small)|at (least|most)|"
    r"minimum|maximum|exceed(s|ed)?|denied|locked|expired|forbidden|unauthori[sz]ed|warning|success(ful(ly)?)?|"
    r"thank(s| you)|confirmed|has been (sent|saved|submitted|created|updated|added|deleted|removed))\b", re.I)
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
        group = next((a for a in _ancestors(n) if a.role in GROUPS), None)
        msg = n.role not in INTERACTIVE and (n.role in MSG_ROLES or (group is not None and group.role in MSG_ROLES)
                                             or bool(MESSAGE.search(label + " " + text)))
        cut_l, cut_t = (MSG_MAX, MSG_MAX) if msg else (80, 100)
        line = n.role + (f' "{label[:cut_l]}"' if label and n.role != "text" else "") + (f": {text[:cut_t]}" if text else "")
        line += "".join(f" [{s}]" for s in st)
        if group is not None and group is not n:
            line += f'  (in {group.role}{" " + repr(group.name) if group.name else ""})'
        out.append(Item((n.role, label[:80]), line, _path(n), n, msg))
    return out


def _path(n):
    p, a = [], n
    while a.parent is not None:
        p.append((a.role, a.parent.children.index(a)))
        a = a.parent
    return tuple(reversed(p))


def _with_card(item, items):
    """The line as shown: with the element's ref when the snapshot has refs (so the LLM can act on a new element,
    e.g. an autocomplete suggestion, without another lookup), and the card title when its role repeats."""
    n = item.node
    head, sep, ctx = item.line.partition("  (in ")
    line = head + (f" [ref={n.ref}]" if n.ref else "") + (sep + ctx if sep else "")
    if n.role not in INTERACTIVE:
        return line
    members = [x.node for x in items if x.node.role == n.role]
    if len(members) < CARD_MIN:
        return line
    card = card_of(n, members) or _heading_before(n, members)
    return line + (f"  (card: {card[:60]})" if card else "")


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
    return out + [f"Message: {m}" for m in messages(snap.snapshot)[:SUMMARY_MESSAGES]]


def messages(snapshot):
    """Feedback texts on a page (alerts, status lines, error / validation / confirmation texts), whole and in page
    order: kept in page summaries so a navigation or a re-rendered view never hides them."""
    out = []
    for x in _items(snapshot):
        if x.msg:
            t = x.node.label() or x.node.text
            if t and t[:MSG_MAX] not in out:
                out.append(t[:MSG_MAX])
    return out


def noise(s1, s2):
    """Tree positions that changed between two snapshots taken without any action (tickers, carousels, timers):
    changes there after an action are not caused by it."""
    a, b = _items(s1), _items(s2)
    sm = SequenceMatcher(None, [x.line for x in a], [x.line for x in b], autojunk=False)
    return {x.path for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal" for x in a[i1:i2] + b[j1:j2]}


def changes(before, after, ignore=frozenset()):
    """Added (+), removed (-) and changed (~) elements between two snapshots of the same page, in page order.
    ignore: tree positions from noise() to leave out (never messages: an error replacing a ticker is still an error)."""
    out, share = _changes(before, after, ignore)
    return [line for line, _ in out], share


def _changes(before, after, ignore=frozenset()):
    a, b = _items(before), _items(after)
    sm = SequenceMatcher(None, [x.line for x in a], [x.line for x in b], autojunk=False)
    lines, changed = [], 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        old = [x for x in a[i1:i2] if x.msg or x.path not in ignore]
        new = [x for x in b[j1:j2] if x.msg or x.path not in ignore]
        changed += max(len(old), len(new))
        olds = {x.key: x for x in old}
        paired = set()
        for x in new:
            if x.key in olds and x.key[1] and x.key not in paired:  # same element, new text / state
                lines.append((f"~ {olds[x.key].line}  ->  {_with_card(x, b)}", x.msg or olds[x.key].msg))
                paired.add(x.key)
            else:
                lines.append((f"+ {_with_card(x, b)}", x.msg))
        lines += [(f"- {_with_card(x, a).split(' [ref=')[0]}", x.msg) for x in old if x.key not in paired]
    return lines, changed / max(1, len(a), len(b))


def _cap(lines):
    """At most MAX_LINES lines, but every message line is kept (they are what a negative test checks)."""
    if len(lines) <= MAX_LINES:
        return [line for line, _ in lines], 0
    room = max(0, MAX_LINES - sum(m for _, m in lines))
    out, dropped = [], 0
    for line, m in lines:
        if m or room > 0:
            out.append(line)
            room -= not m
        else:
            dropped += 1
    return out, dropped


def briefly(before, seen, after):
    """Messages that appeared after the action but were gone again by the end (toasts, flashes): lines to report."""
    have = set(messages(before)) | set(messages(after))
    out = []
    for snap in seen:
        for m in messages(snap):
            if m not in have and m not in out:
                out.append(m)
    return [f"Shown briefly (gone now): {m}" for m in out]


NO_CHANGE = ("No change detected within {secs:g} s. If this action submits, pays or deletes something, do not repeat "
             "it: check the page with browser_snapshot first.")


def outcome(before, after, ignore=frozenset(), waited=0.6, extra=(), seen=()):
    """The result of one action as a few lines of text. extra: lines about new tabs / dialogs, shown first.
    seen: snapshots taken while the page settled (YAML); messages shown only in those are reported as brief."""
    head = list(extra)
    nav = before is None or before.url.split("#")[0] != after.url.split("#")[0]
    brief = briefly(before.snapshot if before else "", seen, after.snapshot)
    if nav:
        return "\n".join(head + ["Navigated to a new page."] + summary(after) + brief +
                         ["Use browser_find_element to locate elements on it."])
    lines, share = _changes(before.snapshot, after.snapshot, ignore)
    if not lines and not brief:
        return "\n".join(head + [NO_CHANGE.format(secs=round(waited, 1))])
    if share > REPLACED and len(_items(after.snapshot)) >= REPLACED_MIN:  # small pages: show the changes themselves
        return "\n".join(head + ["Same URL, but most of the page changed (new view)."] + summary(after) + brief +
                         ["Use browser_find_element to locate elements on it."])
    shown, dropped = _cap(lines)
    more = f"\n(+{dropped} more changes)" if dropped else ""
    return "\n".join(head + ["Same page. Changes:"] + shown + brief) + more


VALIDATION_JS = """() => Array.from(document.querySelectorAll('input, select, textarea'))
  .filter(e => e.willValidate && !e.validity.valid && e.validationMessage && e.getClientRects().length)
  .slice(0, 10)
  .map(e => ({field: (e.getAttribute('aria-label') || (e.labels && e.labels[0] && e.labels[0].innerText) ||
                      e.placeholder || e.name || e.id || e.type || '').trim().slice(0, 80),
              message: e.validationMessage.slice(0, 200)}))"""
VALIDATION_HINT = ("If this click submits a form, the browser's built-in validation may have blocked it: its message is a "
                   "tooltip, not part of the page (this Playwright MCP has no browser_evaluate to read it). Empty or "
                   "malformed required fields are the usual cause.")
VALIDATION_ACTIONS = {"click", "press", "browser_click", "browser_press_key"}


def eval_json(text):
    """The value a browser_evaluate tool returned (Playwright MCP wraps it in markdown; some versions quote it)."""
    res = text.split("### Result", 1)[-1].split("###", 1)[0].strip().strip("`").removeprefix("json").strip()
    val = json.loads(res)
    return json.loads(val) if isinstance(val, str) else val


def validation_lines(fields):
    """Built-in browser form validation ("Please fill out this field.") is a tooltip, not part of the page tree: a
    submit it blocks looks like "no change". These lines say why, with the browser's own messages."""
    fields = [f for f in (fields or []) if isinstance(f, dict) and f.get("message")]
    if not fields:
        return ""
    return "\n".join(["The browser's built-in form validation marks these fields invalid (shown as a tooltip, "
                      "not in the page; a submit is blocked while they are):"] +
                     [f'- "{f.get("field") or "field"}": {f["message"]}' for f in fields])


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
