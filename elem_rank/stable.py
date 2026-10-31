"""Stable locators for generated test code.

Recorded locators often carry database IDs ([data-test="product-01M66W2RA4..."], "#item-8812731") or positional CSS
paths (div > ul > li:nth-of-type(3)). Both break when the data or the layout changes; on demo shops whose data is
reset every few hours, within hours. For such an element we describe it the way a tester would: by its label, the
heading of its card, its table row or its own text, and keep a description only if it matches exactly one element.

STABLE_JS runs in the page on one element and returns the candidate descriptions that are unique there (DOM-level
check); callers turn them into Playwright code with js_code() / py_locator() and, with a live page, confirm the count.
"""
import json
import re

# ULIDs / long upper-case IDs, UUIDs, long numbers: values that change when the data is re-seeded
VOLATILE = re.compile(r"[0-9A-HJKMNP-TV-Z]{20,}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-|\d{6,}")
BRITTLE = re.compile(r"nth-of-type|nth-child| > |\.nth\(")     # positional: breaks when the layout changes
ORDINAL = re.compile(r"\b(first|second|third|last|1st|2nd|3rd)\b", re.I)

STABLE_JS = """(e) => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const out = [];
  const tag = e.tagName.toLowerCase();
  const implicit = {a: 'link', button: 'button', select: 'combobox', textarea: 'textbox', h1: 'heading', h2: 'heading',
    h3: 'heading', h4: 'heading', h5: 'heading', h6: 'heading'};
  const role = e.getAttribute('role') || implicit[tag] || (tag === 'input' ? ({checkbox: 'checkbox', radio: 'radio',
    submit: 'button', button: 'button', number: 'spinbutton', search: 'searchbox'}[e.type] || 'textbox') : '');
  // 1. its label: getByLabel
  const lab = tag === 'label' ? e : (e.labels && e.labels[0]);
  if (lab) {
    const t = norm(lab.innerText);
    if (t && t.length <= 60 && [...document.querySelectorAll('label')].filter(l => norm(l.innerText) === t).length === 1)
      out.push({kind: 'label', text: t});
  }
  // 2. the heading inside it (product cards): locator(tag).filter({has: heading})
  const h = e.querySelector('h1,h2,h3,h4,h5,h6');
  if (h) {
    const t = norm(h.innerText);
    const same = [...document.querySelectorAll(tag)].filter(x => [...x.querySelectorAll('h1,h2,h3,h4,h5,h6')]
      .some(y => norm(y.innerText) === t));
    if (t && t.length <= 80 && same.length === 1) out.push({kind: 'has_heading', tag, text: t});
  }
  // 3. its table row (icon buttons such as delete): row with that first cell, then tag.classes inside it
  const tr = e.closest('tr');
  if (tr && tr !== e) {
    const cell = [...tr.children].find(td => norm(td.innerText) && !td.contains(e));
    const cls = [...e.classList].filter(c => !/^ng-|[0-9]{3,}/.test(c));
    if (cell && cls.length) {
      const key = norm(cell.innerText).slice(0, 60), sub = tag + cls.map(c => '.' + c).join('');
      const rows = [...document.querySelectorAll('tr')].filter(r => norm(r.innerText).includes(key));
      if (rows.length === 1 && rows[0].querySelectorAll(sub).length === 1) out.push({kind: 'row', text: key, sub});
    }
  }
  // 4. role + accessible name (aria-label or short own text)
  const name = norm(e.getAttribute('aria-label') || (tag === 'input' ? '' : e.innerText));
  if (role && name && name.length <= 60) out.push({kind: 'role_name', role, text: name});
  // 5. its own text
  const t = norm(e.innerText);
  if (t && t.length <= 60) out.push({kind: 'text', text: t});
  return JSON.stringify(out);
}"""


def _q(s):
    return json.dumps(s, ensure_ascii=False)


def needs_stable(js, step=""):
    """True when a recorded JavaScript locator would break on new data or a new layout. Positional locators are
    kept when the step itself asks for a position ("the first product")."""
    if VOLATILE.search(js):
        return True
    return bool(BRITTLE.search(js)) and not (".nth(" in js and ORDINAL.search(step or ""))


def js_code(o):
    """Playwright JavaScript locator for one STABLE_JS description."""
    k, t = o["kind"], _q(o.get("text", ""))
    if k == "label":
        return f"page.getByLabel({t}, {{ exact: true }})"
    if k == "has_heading":
        return f"page.locator({_q(o['tag'])}).filter({{ has: page.getByRole('heading', {{ name: {t}, exact: true }}) }})"
    if k == "row":
        return f"page.getByRole('row').filter({{ hasText: {t} }}).locator({_q(o['sub'])})"
    if k == "role_name":
        return f"page.getByRole({_q(o['role'])}, {{ name: {t}, exact: true }})"
    return f"page.getByText({t}, {{ exact: true }})"


def py_code(o):
    """The same locator in Playwright Python syntax."""
    k, t = o["kind"], _q(o.get("text", ""))
    if k == "label":
        return f"page.get_by_label({t}, exact=True)"
    if k == "has_heading":
        return f"page.locator({_q(o['tag'])}).filter(has=page.get_by_role(\"heading\", name={t}, exact=True))"
    if k == "row":
        return f"page.get_by_role(\"row\").filter(has_text={t}).locator({_q(o['sub'])})"
    if k == "role_name":
        return f"page.get_by_role({_q(o['role'])}, name={t}, exact=True)"
    return f"page.get_by_text({t}, exact=True)"


def live(page, o):
    """A live Playwright Locator (sync or async page) for one description, built with Playwright calls."""
    k, t = o["kind"], o.get("text", "")
    if k == "label":
        return page.get_by_label(t, exact=True)
    if k == "has_heading":
        return page.locator(o["tag"]).filter(has=page.get_by_role("heading", name=t, exact=True))
    if k == "row":
        return page.get_by_role("row").filter(has_text=t).locator(o["sub"])
    if k == "role_name":
        return page.get_by_role(o["role"], name=t, exact=True)
    return page.get_by_text(t, exact=True)


def parse(out):
    """STABLE_JS result as returned by page.evaluate (a JSON string) or by a Playwright MCP tool (quoted JSON)."""
    try:
        v = json.loads(out) if isinstance(out, str) else out
        v = json.loads(v) if isinstance(v, str) else v
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def stable_for(page, element, js, step=""):
    """(JavaScript locator, Python locator, live Locator) for an element handle or Locator on a sync Playwright page:
    the recorded `js` when it is robust, else the first stable description that matches exactly one element."""
    if not needs_stable(js, step):
        return js, None, None
    for o in parse(element.evaluate(STABLE_JS)):
        lv = live(page, o)
        try:
            if lv.count() == 1:
                return js_code(o), py_code(o), lv
        except Exception:  # noqa: S112 - an unusable description: try the next one
            continue
    return js, None, None


def rewrite_code(code, old_selector, options):
    """Replace page.locator('<old_selector>') in recorded code (Playwright MCP's "Ran Playwright code" block) with
    the first option. options: STABLE_JS descriptions already confirmed unique."""
    if not options:
        return code
    new = js_code(options[0])
    for lit in (f"page.locator('{old_selector}')", "page.locator(" + _q(old_selector) + ")"):
        code = code.replace(lit, new)
    return code


def _first_unique_sync(page, lv):
    for o in parse(lv.evaluate(STABLE_JS)):
        try:
            if live(page, o).count() == 1:
                return o
        except Exception:  # noqa: S112 - an unusable description: try the next one
            continue
    return None


async def _first_unique_async(page, lv):
    for o in parse(await lv.evaluate(STABLE_JS)):
        try:
            if await live(page, o).count() == 1:
                return o
        except Exception:  # noqa: S112
            continue
    return None


def make_stable(page, hits, step, live_of):
    """Give find() hits whose locator would break (database IDs, positional CSS) a stable one, in place:
    hit["locator"] / hit["py"] become the label / card heading / row / text locator, hit["stable"] keeps it."""
    for h in hits:
        if needs_stable(h["locator"], step):
            try:
                o = _first_unique_sync(page, live_of(page, h))
            except Exception:
                o = None
            if o:
                h.update(locator=js_code(o), py=py_code(o), strategy="stable", stable=o)


async def amake_stable(page, hits, step, live_of):
    for h in hits:
        if needs_stable(h["locator"], step):
            try:
                o = await _first_unique_async(page, live_of(page, h))
            except Exception:
                o = None
            if o:
                h.update(locator=js_code(o), py=py_code(o), strategy="stable", stable=o)
