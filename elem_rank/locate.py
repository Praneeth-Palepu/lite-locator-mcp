"""Library API: shortlist the page elements for a test step, for agents that build their own LLM prompts.

    from lite_locator import Locator
    loc = Locator()                                   # loads the models once (a few seconds)
    hits = loc.find(page, "Enter standard_user in the username field")   # Playwright sync Page
    hits = await loc.afind(page, step)                # Playwright async Page
    hits = loc.find(snapshot_text, step)              # a Playwright MCP snapshot you already have

    hits[0]["locator"]   'page.getByTestId("username")' (Page input, checked on the live page) or hits[0]["ref"]
    hits.prompt          short text for the LLM prompt, instead of the whole accessibility tree
    hits.tokens, hits.full_tokens, hits.fallback_full_tree

No PyTorch and no MCP needed: onnxruntime + tokenizers + numpy, with the models bundled in the package.
"""
import inspect
import json
from pathlib import Path

from .common import doc

JS = (Path(__file__).parent / "extract.js").read_text()
ARIA = {"button", "link", "textbox", "searchbox", "checkbox", "radio", "combobox", "heading", "img", "tab", "menuitem",
        "switch", "slider", "spinbutton", "option", "listitem", "cell", "columnheader"}
STABLE = ("plain", "card-scoped")


def locator(e):
    """A Playwright locator suggestion, most robust first."""
    a, name = e.get("attrs", {}), (e.get("name") or "").replace('"', '\\"')
    if a.get("testid"):
        # getByTestId only reads data-testid (Playwright's default); data-test / data-qa need a CSS attribute locator
        attr = a.get("testid_attr") or "data-testid"
        return f'page.getByTestId("{a["testid"]}")' if attr == "data-testid" else f"page.locator('[{attr}=\"{a['testid']}\"]')"
    if e.get("role") in ARIA and name and len(name) <= 80:
        return f'page.getByRole("{e["role"]}", {{ name: "{name}" }})'
    if a.get("placeholder"):
        return f'page.getByPlaceholder("{a["placeholder"]}")'
    if e.get("kind") == "text" and e.get("text") and len(e["text"]) <= 80:
        return f'page.getByText("{e["text"]}")'
    return f'page.locator("{e.get("css", "")}")'


def _q(t):
    return json.dumps(t, ensure_ascii=False)


def py_locator(e, js=None, how="plain"):
    """The same locator in Playwright Python syntax (runnable with eval(..., {"page": page})). js/how: the resolved
    JavaScript locator and strategy from resolve(), to carry over card scoping and .nth(i)."""
    a, name = e.get("attrs", {}), e.get("name") or ""
    if a.get("testid"):
        attr = a.get("testid_attr") or "data-testid"
        css = "[" + attr + "=" + _q(a["testid"]) + "]"
        base = f'get_by_test_id({_q(a["testid"])})' if attr == "data-testid" else f"locator({_q(css)})"
    elif e.get("role") in ARIA and name and len(name) <= 80:
        base = f'get_by_role({_q(e["role"])}, name={_q(name)}, exact=True)'
    elif a.get("placeholder"):
        base = f'get_by_placeholder({_q(a["placeholder"])}, exact=True)'
    elif e.get("kind") == "text" and e.get("text") and len(e["text"]) <= 80:
        base = f'get_by_text({_q(e["text"])}, exact=True)'
    else:
        base = f'locator({_q(e.get("css", ""))})'
    root = "page"
    if how == "card-scoped":
        root = f'page.locator({_q(e["card_sel"])}).filter(has_text={_q(e["card"])})'
    out = f"{root}.{base}"
    if how.startswith("positional") and js and js.endswith(")") and ".nth(" in js:
        out += js[js.rindex(".nth("):]
    return out


def _base(root, e):
    """The plain locator (same preference order as locator()) under `root` (the page or a scoping locator)."""
    a, name = e.get("attrs", {}), e.get("name") or ""
    if a.get("testid"):
        attr = a.get("testid_attr") or "data-testid"
        return root.get_by_test_id(a["testid"]) if attr == "data-testid" else root.locator(f'[{attr}="{a["testid"]}"]')
    if e.get("role") in ARIA and name and len(name) <= 80:
        return root.get_by_role(e["role"], name=name, exact=True)
    if a.get("placeholder"):
        return root.get_by_placeholder(a["placeholder"], exact=True)
    if e.get("kind") == "text" and e.get("text") and len(e["text"]) <= 80:
        return root.get_by_text(e["text"], exact=True)
    return root.locator(e["css"]) if e.get("css") else None


def _steps(page, e):
    """Generic locator fallbacks as a generator of Playwright calls, so one body serves the sync and async APIs:
    1. plain (test id / role+name / placeholder / text / css)
    2. card-scoped, for repeated labels: container.filter({hasText: card title}) + the plain locator inside it
    3. positional .nth(i) among the plain matches (always unique, but breaks if the page order changes)
    Yields awaitables/values to evaluate; returns (locator string, live match count, strategy)."""
    s = locator(e)
    loc = _base(page, e)
    n = (yield loc.count()) if loc else 0
    if n == 1:
        return s, 1, "plain"
    if e.get("card") and e.get("card_sel"):
        scope = page.locator(e["card_sel"]).filter(has_text=e["card"])
        if (yield scope.count()) == 1 and (yield _base(scope, e).count()) == 1:
            card = e["card"].replace('"', '\\"')
            return f'page.locator("{e["card_sel"]}").filter({{ hasText: "{card}" }})' + s[len("page"):], 1, "card-scoped"
    if n > 1:
        k = yield loc.evaluate_all("(els, idx) => els.findIndex(x => x.getAttribute('data-er-idx') === idx)", str(e["idx"]))
        if k >= 0:
            return f"{s}.nth({k})", 1, "positional (brittle)"
    return s, n, "unresolved"


def resolve(page, e):
    """(locator string, live match count, strategy) on a sync Playwright page."""
    g = _steps(page, e)
    try:
        v = next(g)
        while True:
            v = g.send(v)
    except StopIteration as r:
        return r.value
    except Exception:
        return locator(e), 0, "unresolved"


async def aresolve(page, e):
    """resolve() for an async Playwright page."""
    g = _steps(page, e)
    try:
        v = next(g)
        while True:
            v = g.send(await v)
    except StopIteration as r:
        return r.value
    except Exception:
        return locator(e), 0, "unresolved"


CLICK_EVENTS = {"click", "mousedown", "mouseup", "pointerdown", "pointerup", "touchstart", "touchend"}
CLICK_HOPS = 3  # a listener on the element or up to 3 parents (Disney's sits on the parent of the "English" div)


def _mark_clickable(els, listeners, tree):
    """Flag text elements that have a click listener on themselves or a near parent: JS-only controls the
    accessibility tree shows as plain text. Listeners on body / html / document (one handler for the whole page,
    common in React and Angular) are ignored, else everything would look clickable."""
    on = {x["backendNodeId"] for x in listeners if x.get("type") in CLICK_EVENTS and x.get("backendNodeId")}
    parent, idx, stack = {}, {}, [(tree["root"], None)]
    while stack:
        n, par = stack.pop()
        parent[n["backendNodeId"]] = par
        a = n.get("attributes") or []
        if "data-er-idx" in a:
            idx[int(a[a.index("data-er-idx") + 1])] = n
        for c in (n.get("children") or []) + (n.get("shadowRoots") or []):
            stack.append((c, n))
    for e in els:
        n = idx.get(e["idx"])
        if e["kind"] == "interactive" or n is None:
            continue
        for _ in range(CLICK_HOPS + 1):
            if n is None or n.get("nodeName") in ("BODY", "HTML", "#document"):
                break
            if n["backendNodeId"] in on:
                e["clickable"] = True
                break
            n = parent.get(n["backendNodeId"])


def _listeners_sync(page, els):
    try:  # Chromium only (CDP); other browsers simply skip the signal
        cdp = page.context.new_cdp_session(page)
        doc = cdp.send("Runtime.evaluate", {"expression": "document"})["result"]["objectId"]
        ls = cdp.send("DOMDebugger.getEventListeners", {"objectId": doc, "depth": -1, "pierce": True})["listeners"]
        _mark_clickable(els, ls, cdp.send("DOM.getDocument", {"depth": -1, "pierce": True}))
        cdp.detach()
    except Exception:
        pass


async def _listeners_async(page, els):
    try:
        cdp = await page.context.new_cdp_session(page)
        doc = (await cdp.send("Runtime.evaluate", {"expression": "document"}))["result"]["objectId"]
        ls = (await cdp.send("DOMDebugger.getEventListeners", {"objectId": doc, "depth": -1, "pierce": True}))["listeners"]
        _mark_clickable(els, ls, await cdp.send("DOM.getDocument", {"depth": -1, "pierce": True}))
        await cdp.detach()
    except Exception:
        pass


class Hits(list):
    """The shortlist (best first) plus what an agent needs to build its prompt."""
    prompt: str
    tokens: int
    full_tokens: int
    total: int
    fallback_full_tree: bool  # True: don't trust the shortlist alone for this step, send the LLM the full tree


class Locator:
    def __init__(self, lam=0.01, model_dir=None):
        """lam: the RL cut policy's price per candidate (0.005 = more candidates and higher recall, 0.02 = fewer).
        model_dir: an ONNX bundle folder; default is the one shipped in the package."""
        from .lite import LiteRanker, bundle_dir
        self.lam, self.rk = lam, LiteRanker(model_dir or bundle_dir())

    def _rank(self, step, docs, cap, els=None):
        s, lg = self.rk.rank(step, docs, ("page", hash(tuple(docs))))  # same page state -> embeddings reused
        if els is not None:
            from .snapshot import demote_orphans
            s = demote_orphans(s.copy(), els)
        k = self.rk.cut(lg, self.lam) or 5
        k = min(k, cap) if cap else k
        return sorted(range(len(docs)), key=lambda i: -s[i])[:k], s

    def _snapshot(self, text, step, cap):
        from .snapshot import elements, line
        els = elements(text)
        docs = [doc(e) for e in els]
        top, s = self._rank(step, docs, cap, els) if els else ([], None)
        hits = Hits({"rank": r + 1, "score": round(float(s[i]), 3), "ref": els[i]["ref"], "role": els[i]["role"],
                     "name": els[i]["name"], "card": els[i].get("card", ""), "line": line(els[i])}
                    for r, i in enumerate(top))
        best = els[top[0]] if top else None
        dup = best and not best.get("card") and sum(
            e["role"] == best["role"] and e["text"].lower() == best["text"].lower() for e in els) > 1
        return self._finish(hits, [h["line"] for h in hits], docs, not hits or bool(dup))

    def _finish(self, hits, lines, docs, fallback):
        hits.prompt = "\n".join(lines)
        hits.tokens, hits.full_tokens = len(hits.prompt) // 4, sum(len(d) for d in docs) // 4
        hits.total, hits.fallback_full_tree = len(docs), fallback
        return hits

    def _page_hits(self, els, top, s, resolved):
        hits = Hits()
        for r, (i, (loc, n, how)) in enumerate(zip(top, resolved)):
            e = els[i]
            hits.append({"rank": r + 1, "score": round(float(s[i]), 3), "locator": loc, "py": py_locator(e, loc, how),
                         "matches": n, "strategy": how,
                         "role": e["role"], "name": e.get("name") or "", "card": e.get("card", ""),
                         "clickable": bool(e.get("clickable")), "doc": doc(e)})
        lines = [f'{h["rank"]}. {h["doc"]}\n   locator: {h["locator"]}' for h in hits]
        return hits, lines

    def find(self, page_or_snapshot, step, max=None, verify=True, max_elements=1500):
        """Shortlist for one step. page_or_snapshot: a Playwright sync Page, or Playwright MCP snapshot text.
        verify: check each locator on the live page and fall back (card-scoped, then positional) when not unique."""
        if isinstance(page_or_snapshot, str):
            return self._snapshot(page_or_snapshot, step, max)
        page = page_or_snapshot
        if inspect.iscoroutinefunction(page.evaluate):
            raise TypeError("async Playwright page: use `await locator.afind(page, step)`")
        els = page.evaluate(JS, max_elements)["elements"]
        _listeners_sync(page, els)
        docs = [doc(e) for e in els]
        if not els:
            return self._finish(Hits(), [], docs, True)
        top, s = self._rank(step, docs, max)
        resolved = [resolve(page, els[i]) if verify else (locator(els[i]), None, "unverified") for i in top]
        hits, lines = self._page_hits(els, top, s, resolved)
        return self._finish(hits, lines, docs, verify and hits[0]["strategy"] not in STABLE)

    async def afind(self, page_or_snapshot, step, max=None, verify=True, max_elements=1500):
        """find() for an async Playwright Page (snapshot text works too)."""
        if isinstance(page_or_snapshot, str):
            return self._snapshot(page_or_snapshot, step, max)
        page = page_or_snapshot
        els = (await page.evaluate(JS, max_elements))["elements"]
        await _listeners_async(page, els)
        docs = [doc(e) for e in els]
        if not els:
            return self._finish(Hits(), [], docs, True)
        top, s = self._rank(step, docs, max)
        resolved = [await aresolve(page, els[i]) if verify else (locator(els[i]), None, "unverified") for i in top]
        hits, lines = self._page_hits(els, top, s, resolved)
        return self._finish(hits, lines, docs, verify and hits[0]["strategy"] not in STABLE)


# ---------- what an action changed (instead of full snapshots) ----------

def observe(page):
    """The page state to compare against after an action (sync Playwright page)."""
    from .diff import Snap
    return Snap(page.url, page.title(), page.locator("body").aria_snapshot())


async def aobserve(page):
    from .diff import Snap
    return Snap(page.url, await page.title(), await page.locator("body").aria_snapshot())


def _target(page, hit):
    return eval(hit["py"] if isinstance(hit, dict) else hit, {"page": page})  # a find() hit or its "py" string


def act(page, hit, action, value=None, settle=600):
    """Do one action on a find() hit (or a Python locator string) and return what changed, as a few lines:
    navigation summary, the added/removed/changed elements, or "No visible change".
    action: click | dblclick | hover | fill | type | select | check | uncheck | press."""
    from .diff import outcome
    before, el = observe(page), _target(page, hit)
    {"click": lambda: el.click(), "dblclick": lambda: el.dblclick(), "hover": lambda: el.hover(),
     "fill": lambda: el.fill(value or ""), "type": lambda: el.press_sequentially(value or ""),
     "select": lambda: el.select_option(label=value), "check": lambda: el.check(), "uncheck": lambda: el.uncheck(),
     "press": lambda: el.press(value or "Enter")}[action]()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(settle)
    return outcome(before, observe(page))


async def aact(page, hit, action, value=None, settle=600):
    """act() for an async Playwright page."""
    from .diff import outcome
    before, el = await aobserve(page), _target(page, hit)
    run = {"click": el.click, "dblclick": el.dblclick, "hover": el.hover, "check": el.check, "uncheck": el.uncheck,
           "fill": lambda: el.fill(value or ""), "type": lambda: el.press_sequentially(value or ""),
           "select": lambda: el.select_option(label=value), "press": lambda: el.press(value or "Enter")}[action]
    await run()
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(settle)
    return outcome(before, await aobserve(page))
