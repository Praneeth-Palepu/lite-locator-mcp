"""Shared element representation: one text 'doc' per candidate element, used by every ranker."""
import json
import os
import re
from pathlib import Path

# ELEM_RANK_HOME: folder holding models/ (and data/ caches) for an installed copy; defaults to the repo checkout
ROOT = Path(os.environ.get("ELEM_RANK_HOME") or Path(__file__).resolve().parent.parent)
DATA = ROOT / "data"


def humanize(s):
    """'first_name' / 'firstName' / 'txtFirstName' -> 'first name'."""
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s or "")
    s = re.sub(r"[_\-\.\[\]]+", " ", s)
    s = re.sub(r"^(txt|tb|btn|lbl|ddl|chk|rb|input|inp)\s+", "", s.strip(), flags=re.I)
    return re.sub(r"\s+", " ", s).strip().lower()


def label_of(e):
    """Best human label for an element: accessible name, else placeholder / nearby text / humanized attribute."""
    a = e.get("attrs", {})
    for v in (e.get("name"), a.get("aria_label"), a.get("placeholder"), a.get("title"), a.get("alt")):
        if v and v.strip():
            return v.strip()
    if e.get("role") in ("textbox", "searchbox", "combobox", "checkbox", "radio", "spinbutton") and e.get("near"):
        return e["near"][:60]
    for v in (a.get("name"), a.get("id"), a.get("testid")):
        if v and humanize(v):
            return humanize(v)
    return (e.get("text") or "").strip()[:80]


def doc(e, max_chars=360):
    """Compact text view of an element: role, name, text, key attributes and context."""
    a = e.get("attrs", {})
    role = e.get('role') or e.get('tag')
    if e.get("clickable"):  # plain text with a click handler (no button role): a JS-only control
        role += ", clickable"
    parts = [f"[{role}] {e.get('name') or ''}".strip()]
    t = (e.get("text") or "").strip()
    if t and t.lower() != (e.get("name") or "").lower():
        parts.append("text: " + t[:120])
    kv = [f"{k}={humanize(a[k]) if k in ('id', 'name', 'testid') else a[k]}" for k in
          ("type", "placeholder", "name", "id", "testid", "aria_label", "title", "alt", "value", "href") if a.get(k)]
    if kv:
        parts.append(" ".join(kv)[:160])
    if e.get("card"):  # only set for repeated labels ("Buy Now" x6): which card this copy belongs to
        parts.append("card: " + e["card"][:80])
    if e.get("near"):
        parts.append("near: " + e["near"][:60])
    if e.get("heading"):
        parts.append("section: " + e["heading"][:60])
    if e.get("landmark"):
        parts.append("in: " + e["landmark"])
    return " | ".join(parts)[:max_chars]


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def equivalents(elements, i):
    """Indices that are indistinguishable from element i for a user (same role and same name), e.g. two
    'Login' links in header and footer. Any of them counts as a correct answer."""
    e = elements[i]
    key = (e.get("role"), norm(e.get("name")) or norm(label_of(e)))
    if not key[1]:
        return [i]
    return [j for j, x in enumerate(elements) if (x.get("role"), norm(x.get("name")) or norm(label_of(x))) == key]


def load_jsonl(p):
    with open(p) as f:
        return [json.loads(l) for l in f if l.strip()]


def write_jsonl(p, rows):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
