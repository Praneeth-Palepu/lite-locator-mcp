"""Parse a Playwright MCP accessibility snapshot (the YAML with [ref=eN]) into ranker elements.

Each element gets the same context fields the in-page extractor (extract.js) gives the models: section heading,
landmark, the text just before it, and, for repeated labels ("Buy Now" x6), the title of its own card.
"""
import json
import re

LINE = re.compile(r'^(?P<ind>\s*)- (?P<body>.*)$')
NODE = re.compile(r'^(?P<role>[A-Za-z]+)(?: "(?P<name>(?:[^"\\]|\\.)*)")?(?P<attrs>(?: \[[^\]]*\])*)(?::\s*(?P<text>.*))?$')
ATTR = re.compile(r'\[([^\]=]+)(?:=([^\]]*))?\]')
INTERACTIVE = {"button", "link", "textbox", "searchbox", "combobox", "checkbox", "radio", "tab", "menuitem", "switch",
               "option", "slider", "spinbutton", "menuitemcheckbox", "menuitemradio", "treeitem"}
LANDMARKS = {"navigation", "banner", "contentinfo", "main", "form", "dialog", "search", "complementary", "region"}
CONTAINERS = {"generic", "group", "list", "listitem", "region", "main", "navigation", "banner", "contentinfo",
              "complementary", "article", "section", "document", "table", "rowgroup", "row"}


class Node:
    __slots__ = ("role", "name", "ref", "attrs", "text", "children", "parent", "href")

    def __init__(self, role, name, ref, attrs, text, parent):
        self.role, self.name, self.ref, self.attrs, self.text = role, name, ref, attrs, text
        self.children, self.parent, self.href = [], parent, ""

    def label(self):
        return self.name or self.text or " ".join(c.text for c in self.children if c.role == "text" and c.text)

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


def _unquote(t):
    """Snapshot values may be quoted, JSON-style ("\\"2026 Guidelines\\" See details")."""
    t = t.strip()
    if len(t) >= 2 and t[0] == t[-1] == '"':
        try:
            return json.loads(t)
        except ValueError:
            return t[1:-1]
    return t


def _unquote_key(b):
    """A node line that YAML had to quote because its text contains ': ' ('link "CO₂: A B C" [ref=e9]':) or starts
    with a special character: remove the quotes (keeping the trailing ':' of a node with children)."""
    if len(b) >= 2 and b[0] in "'\"":
        q = b[0]
        tail = ":" if b.endswith(q + ":") else ""
        inner = b[1:len(b) - 1 - len(tail)] if b.endswith(q + tail) else None
        if inner is not None:
            if q == "'":
                return inner.replace("''", "'") + tail
            try:
                return json.loads(q + inner + q) + tail
            except ValueError:
                return inner + tail
    return b


def parse_tree(snapshot):
    """Nodes of the YAML block, nested by indentation."""
    body = snapshot.split("```yaml", 1)[1].split("```", 1)[0] if "```yaml" in snapshot else snapshot
    root = Node("document", "", None, {}, "", None)
    stack = [(-1, root)]
    for line in body.splitlines():
        m = LINE.match(line)
        if not m:
            continue
        ind, b = len(m["ind"]), m["body"].strip()
        while stack and stack[-1][0] >= ind:
            stack.pop()
        parent = stack[-1][1]
        if b.startswith("/url:"):
            parent.href = b[5:].strip()
            continue
        if b.startswith("text:"):
            parent.children.append(Node("text", "", None, {}, _unquote(b[5:]), parent))
            continue
        n = NODE.match(_unquote_key(b))
        if not n:
            continue
        attrs = {k: (v if v is not None else True) for k, v in ATTR.findall(n["attrs"] or "")}
        text = _unquote(n["text"] or "")
        node = Node(n["role"], (n["name"] or "").replace('\\"', '"'), attrs.pop("ref", None), attrs, text, parent)
        parent.children.append(node)
        stack.append((ind, node))
    return root


def _first_heading(node, skip):
    for d in node.walk():
        if d is not skip and d.role == "heading" and d.label() and skip not in d.walk():
            return d.label()
    return ""


def _near(node):
    """Label-like text just before the node (previous sibling, up to 3 levels), as extract.js prevText does."""
    n, hops = node, 0
    while n.parent is not None and hops < 3:
        sibs = n.parent.children
        for s in reversed(sibs[:sibs.index(n)]):
            t = s.label()
            if t:
                return t[:80]
        n, hops = n.parent, hops + 1
    return ""


def _noise(n):
    """Non-interactive text that cannot be a test target on its own: under 3 characters, or only digits, currency
    and punctuation ("¥", "and", "1 / 1"). Interactive elements are always kept."""
    if n.role in INTERACTIVE or n.attrs.get("cursor"):
        return False
    t = n.label().strip()
    if len(t) < 3 or re.fullmatch(r"[\d\s¥$€£₹.,:;/|()+%-]+", t):
        return True
    return t.lower().strip(" ,.;:&|-") in {"and", "or", "the", "of", "&", ""}


CHOICE = {"combobox", "listbox", "radiogroup", "menu", "tablist"}
ORPHAN_IN = {"banner", "navigation"}
ORPHAN_PENALTY = 2.0  # fusion-score handicap: text without its own element wins only when no real element fits


def demote_orphans(scores, els):
    """Plain-text candidates (orphans) are a fallback for JavaScript-only controls; without the handicap a value in the
    step ("enter standard_user ...") can match a hint text on the page above the real textbox."""
    for i, e in enumerate(els):
        if e.get("orphan"):
            scores[i] -= ORPHAN_PENALTY
    return scores
ORPHAN_MAX = 30


def orphan_texts(root, anywhere=False):
    """Short header / nav text that has no element of its own, e.g. a JavaScript language switcher that is only a
    div showing "English": the snapshot gives it no ref, so it is clicked through its nearest ancestor that has one.
    Returns (text node, text, landmark, nearest ancestor ref or None)."""
    out = []
    for n in root.walk():
        if n.role != "text" or not (2 < len(n.text.strip()) <= ORPHAN_MAX) or _noise(n):
            continue
        land, ref, a = None, None, n.parent
        while a is not None:
            if a.role in INTERACTIVE:
                break  # text inside a link / button belongs to that element
            if a.ref and ref is None:
                ref = a.ref  # duplicates of real candidates are removed in elements() (same text)
            if a.role in ORPHAN_IN:
                land = a.role + (":" + a.name[:40] if a.name else "")
                break
            a = a.parent
        if land or anywhere:  # anywhere: also pages whose header has no landmark at all (Shanghai Disney)
            out.append((n, n.text.strip(), land or "", ref))
    return out


def card_of(node, members):
    """Title of the card that holds `node`: the largest ancestor containing no other of `members` (look-alikes such
    as the other "Add to cart" buttons), titled by its first heading, else its first other meaningful label."""
    box = node
    while box.parent is not None and sum(m in set(box.parent.walk()) for m in members) == 1:
        box = box.parent
    if box is node:
        return ""
    own = node.label()
    return _first_heading(box, node) or next(
        (d.label() for d in box.walk() if d is not node and d.label() and d.label() != own and len(d.label()) >= 6
         and not re.fullmatch(r"[\d\s¥$€£₹.,:/-]+", d.label()) and node not in set(d.walk())), "")


def elements(snapshot):
    """Rankable elements: every node with a ref that carries a label or is interactive."""
    root = parse_tree(snapshot)
    nodes = [n for n in root.walk() if n.ref and (n.label() or n.role in INTERACTIVE)
             and not (n.role in CONTAINERS and not n.name and not n.text) and not _noise(n)]
    out = []
    for n in nodes:
        heading, land, a = "", "", n.parent
        for _ in range(8):
            if a is None:
                break
            heading = heading or _first_heading(a, n)
            if not land and a.role in LANDMARKS:
                land = a.role + (":" + a.name[:40] if a.name else "")
            a = a.parent
        text = n.label()
        if n.role in CHOICE:  # dropdowns / lists: their options are child nodes; the step usually names one of them
            opts = [c.label() for c in n.walk() if c.role in ("option", "menuitem", "radio") and c.label()]
            text = (text + " " if text and text != n.name else "") + " ".join(opts)
        out.append({"ref": n.ref, "role": n.role, "name": n.name, "text": text[:200],
                    "attrs": {"href": n.href} if n.href else {}, "near": _near(n), "heading": heading[:80],
                    "landmark": land, "kind": "interactive" if n.role in INTERACTIVE or n.attrs.get("cursor") else "text",
                    "_node": n})
    # card context for repeated labels: the largest ancestor that still holds only this copy, titled by its first
    # heading, else its first other meaningful label (same rule as extract.js)
    groups = {}
    for i, e in enumerate(out):
        if e["text"]:
            groups.setdefault((e["role"], e["text"].lower()), []).append(i)
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        members = [out[i]["_node"] for i in idxs]
        for i in idxs:
            title = card_of(out[i]["_node"], members)
            if title:
                out[i]["card"] = title[:90]
    have = {e["text"].lower() for e in out}
    for n, t, land, ref in orphan_texts(root, anywhere=True):
        if ref and t.lower() not in have:  # clickable only through an ancestor ref
            have.add(t.lower())
            out.append({"ref": ref, "role": "text", "name": "", "text": t, "attrs": {}, "near": _near(n),
                        "heading": "", "landmark": land, "kind": "text", "orphan": True})
    for e in out:  # position in a repeated list (cards, results): for "open the first product"
        n = e.get("_node")
        if n is not None and e["role"] == "link":
            e["list"] = _list_slot(n)
    for e in out:
        e.pop("_node", None)
    return out


def _list_slot(node, depth=4):
    """(id of the list container, position) when the node, or one of its near ancestors, is one of 3+ siblings of
    the same role: a card in a grid or an item in a result list."""
    a = node
    for _ in range(depth):
        if a.parent is None:
            break
        same = [c for c in a.parent.children if c.role == a.role]
        if len(same) >= 3:
            return id(a.parent), same.index(a)
        a = a.parent
    return None


def line(e):
    """How a candidate is shown to the LLM: the snapshot's own notation plus the context that disambiguates it."""
    if e.get("orphan"):  # plain text without its own element: the ref is the container that holds it
        return f'- text "{e["text"]}" [ref={e["ref"]}]  (plain text, click via its container ref' + (
            f'; in: {e["landmark"]})' if e["landmark"] else ")")
    s = f'- {e["role"]}' + (f' "{e["name"]}"' if e["name"] else "") + f' [ref={e["ref"]}]'
    if e["text"] and e["text"] != e["name"]:
        s += f': {e["text"][:100]}'
    ctx = [f'card: {e["card"]}'] if e.get("card") else []
    if e["landmark"]:
        ctx.append(f'in: {e["landmark"]}')
    elif e["heading"]:
        ctx.append(f'section: {e["heading"]}')
    return s + (f'  ({"; ".join(ctx)})' if ctx else "")


# ---------- unnamed buttons / links (icon buttons, controls whose text is hidden from the accessibility tree) ----------

DETAIL_ROLES = {"button": "button, [role=button], input[type=button], input[type=submit], input[type=reset]",
                "link": "a[href], [role=link]"}
DETAILS_JS = """() => {
  const sels = %s;
  const vis = (el) => { const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const out = {};
  for (const [role, sel] of Object.entries(sels)) {
    out[role] = [...document.querySelectorAll(sel)].filter(vis).map(el => ({
      testid: el.getAttribute('data-testid') || el.getAttribute('data-test') || el.getAttribute('data-qa') || '',
      id: el.id || '', title: el.getAttribute('title') || '', text: (el.innerText || el.value || '').trim().slice(0, 80),
      icon: [...el.querySelectorAll('[class*=icon], [class*=fa-], svg')].map(i => (i.getAttribute('class') || '')
        .split(/\\s+/).filter(c => /icon|fa-|cart|search|user|menu|close/i.test(c)).join(' ')).join(' ').slice(0, 60),
      href: el.getAttribute('href') || '' }));
  }
  return JSON.stringify(out);
}""" % json.dumps(DETAIL_ROLES)


def needs_details(els):
    return any(e["role"] in DETAIL_ROLES and not e["name"] and not e["text"] for e in els)


def merge_details(els, details):
    """Describe unnamed buttons / links from their DOM attributes (test id, id, title, inner text, icon classes).
    The snapshot's and the page's elements of a role are matched in page order, and only when the counts agree
    exactly; otherwise nothing is added (a wrong description would be worse than none)."""
    for role, items in (details or {}).items():
        snap = [e for e in els if e["role"] == role and not e.get("orphan")]
        if len(snap) != len(items):
            continue
        for e, d in zip(snap, items, strict=True):
            if e["name"] or e["text"]:
                continue
            words = d.get("text") or d.get("title") or " ".join(
                x for x in (d.get("testid"), d.get("id"), d.get("icon")) if x).replace("-", " ").replace("_", " ")
            if words:
                e["text"] = words[:80]
                e["attrs"] = {**e.get("attrs", {}), **{k: d[k] for k in ("testid", "id", "title") if d.get(k)}}
    return els
