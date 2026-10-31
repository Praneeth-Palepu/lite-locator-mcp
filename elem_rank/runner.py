"""Plan once, execute with the library: a manual test case is run with a handful of small LLM calls instead of an
agent loop that re-sends the whole conversation on every step.

1. plan: one LLM call turns the test case into structured steps (open / click / type / select / check / press /
   back / verify_*). Values are taken from the test case text, never from the model's copy of it.
2. execute: for each action the library finds the element itself (find + rank) and acts when it is sure: the top
   match is clearly ahead of the second, fits the action (type -> a text box) and resolves to one element, or its
   own title appears in the step ("Claw Hammer", not "Claw Hammer with Shock Reduction Grip"). Otherwise the LLM
   picks from the shortlist (a ~300-token prompt). A click that changes nothing goes back to the LLM once.
3. verify steps are checked in the browser by code; a value mismatch is shown to the LLM before the step fails, in
   case the wrong element was read.
4. the result carries a Playwright test (JavaScript) with stable locators and waits on what each action changed
   (new page title, a badge that went from 1 to 2), plus the LLM usage.

    from lite_locator.runner import TestRunner
    runner = TestRunner(llm)                    # a LangChain chat model, or any function: prompt -> text
    with sync_playwright() as p:
        page = p.chromium.launch().new_page()
        result = runner.run(page, open("case.txt").read())
    print(result.passed, result.usage)
    open("case.spec.js", "w").write(result.spec("Login and add to cart"))

A failed case can be run again with the LLM choosing every element: TestRunner(llm, strict=True).
"""
import difflib
import json
import re
import time
from dataclasses import dataclass, field

from .locate import Locator, act, to_locator
from .stable import ORDINAL, VOLATILE, stable_for

PLAN = """Convert the manual test case into JSON steps for a test runner. Ops:
open(value=url) | click(target) | type(target, value) | select(target, value=option label) | check(target) |
uncheck(target) | press(value=key, e.g. Enter, Tab) | back | verify_text(value=text that must be visible) |
verify_absent(value=text that must not be shown) | verify_url(value=part of the URL) |
verify_value(target, value=expected value or text of that one element)
"step" is the original sentence. target describes the element in plain words. "X is listed / shown" -> verify_text
with X; "is not listed" / "no longer listed" -> verify_absent; an exact message in quotes -> verify_text with that
message; "the field / badge / first item shows N" -> verify_value. A verify value is only the text expected on the page
(a message, a name, a price such as $28.30), never words that say where it is ("line total", "the cart shows"),
copied word for word from the test case; one verify step per expected text ("name X, price Y" = two steps).
Test cases come in any format ("Steps | Expected Result" rows, Gherkin Given/When/Then, prose, abbreviations): turn
every action and every expected result into steps, in order. Preconditions ("Pre-req: X is in the cart", "Given I have
opened X") become the steps that create that state. Every step is ONE action on ONE element, so a sentence that does
several things becomes several steps, with nothing left out:
- "Add X to the cart" = click the product X, then click the Add to cart button (then accept a popup if one is mentioned)
- "Login as U / P" = click the login link, type U in the username field, type P in the password field, click the
  Log in button
- "Send the message M" or "submit the form" = type each field, then click the send / submit button
- "Go to checkout" = click the cart link, then click the checkout / place order button
- "any" / "some" item (no name given) = the first one listed
Use open only with a full URL; going to a page by its link ("open the cart") is a click.
Example (made-up values; take every real value from the test case, never from this example):
{"steps": [{"step": "Type blue widget in the search box", "op": "type", "target": "search box", "value": "blue widget"},
{"step": "Verify the product Example Item is not listed", "op": "verify_absent", "value": "Example Item"}]}
Answer with JSON only.

Test case:
"""
CHOOSE = """Test step: {step}
Candidate elements on the page:
{cands}
{note}Reply with only the number of the element this step acts on, or 0 if none fits."""

REACH = """Test step: {step}
The element this step needs is not on the page yet: it is probably inside a dialog or menu that must be opened first,
or on another page (e.g. a product page). Elements on the page now:
{cands}
Reply with only the number of the ONE element to click first to get there, or 0 if none of them leads there."""

ANY = re.compile(r"\b(?:any|some|a random|whichever)\b(?! of the following)", re.I)

FIT = {"type": {"textbox", "searchbox", "spinbutton", "combobox"}, "select": {"combobox", "listbox"},
       "check": {"checkbox", "radio", "switch"}, "uncheck": {"checkbox", "switch"}}
ACTION = {"click": "click", "type": "fill", "select": "select", "check": "check", "uncheck": "uncheck"}
CODE = {"click": ".click()", "fill": ".fill({v})", "select": ".selectOption({{ label: {v} }})", "check": ".check()",
        "uncheck": ".uncheck()"}
# short visible texts of test-id elements: what an action changed (a badge 1 -> 2), to wait on in the script
MARKS_JS = """() => { const out = {}, seen = new Set();
  for (const e of document.querySelectorAll('[data-test],[data-testid],[data-qa]')) {
    const k = ['data-test', 'data-testid', 'data-qa'].find(a => e.getAttribute(a) !== null);
    const key = k + '=' + e.getAttribute(k);
    if (seen.has(key)) { delete out[key]; continue; } seen.add(key);
    const t = (e.innerText || '').trim(); if (t.length && t.length <= 40 && e.offsetParent !== null) out[key] = t; }
  return out }"""
TITLE_JS = """e => { const x = e.querySelector('h1,h2,h3,h4,h5,h6');
  const lab = (e.labels && e.labels[0]) ? e.labels[0].innerText : '';
  return (x ? x.innerText : (lab || e.getAttribute('aria-label') || e.innerText || '')).trim() }"""


def _q(s):
    return json.dumps(s, ensure_ascii=False)


def _respan(v, text):
    """The test case's own spelling of a value the model copied with changed spacing ("$28. 30" -> "$28.30"); the
    value unchanged when it is not in the text at all."""
    want = re.sub(r"\s+", "", v).lower()
    chars = [(i, c) for i, c in enumerate(text) if not c.isspace()]
    flat = "".join(c for _, c in chars).lower()
    k = flat.find(want)
    if want and k >= 0:
        return text[chars[k][0]:chars[k + len(want) - 1][0] + 1]
    # garbled copy ("Slip Joint P's"): the closest run of words in the step, if it is close enough
    words = text.split()
    best, score = v, 0.0
    for i in range(len(words)):
        for j in range(i + 1, min(len(words), i + 12) + 1):
            cand = " ".join(words[i:j]).strip(".,;:")
            r = difflib.SequenceMatcher(None, cand.lower(), v.lower()).ratio()
            if r > score:
                best, score = cand, r
    return best if score >= 0.75 else v


@dataclass
class Result:
    """Outcome of one test case: per-step log, the recorded script lines and the LLM usage."""
    steps: list
    log: list
    lines: list
    usage: dict = field(default_factory=dict)
    popups: list = field(default_factory=list)      # browser dialog messages seen during the run

    @property
    def passed(self):
        return bool(self.log) and all(x["ok"] for x in self.log)

    def spec(self, title="test"):
        """The Playwright test (JavaScript) for this run. A failed run gives a test marked fixme, with the failing
        step as a comment, so an incomplete script never passes by accident."""
        lines = self.lines
        if self.popups or any("dialogs" in x for x in lines):     # popups: accept them, keep their text
            lines = ["const dialogs = [];", "page.on('dialog', d => { dialogs.push(d.message()); d.accept(); });"] + lines
        body = "\n".join("  " + x for x in lines)
        fn = "test"
        if not self.passed:
            bad = next((x for x in self.log if not x["ok"]), None)
            fn = "test.fixme"
            if bad:
                info = bad["info"][:160].replace("\n", " ")
                body += f"\n  // NOT RECORDED: step failed: {bad['step']}: {info}"
        return (f"import {{ test, expect }} from '@playwright/test';\n\n"
                f"{fn}({_q(title)}, async ({{ page }}) => {{\n{body}\n}});\n")


class TestRunner:
    __test__ = False    # not a pytest test class

    def __init__(self, llm, locator=None, margin=1.0, strict=False, wait=5.0, planner=None):
        """llm: a LangChain chat model (anything with .invoke returning a message) or a function prompt -> text.
        planner: a different model for the one planning call per test case (e.g. the same model in JSON mode).
        margin: how far (fused z-score units) the top match must lead the second to act without the LLM.
        strict: the LLM chooses every element (for re-running a failed case)."""
        self.llm, self.loc, self.margin, self.strict, self.wait = llm, locator or Locator(), margin, strict, wait
        self.planner, self.usage, self.last_change, self.dialogs = planner or llm, {}, "", []

    # ---------- LLM ----------
    def ask(self, prompt, kind):
        llm = self.planner if kind == "plan" else self.llm
        if hasattr(llm, "invoke"):
            m = llm.invoke(prompt)
            text, meta = getattr(m, "content", str(m)), getattr(m, "usage_metadata", None) or {}
        else:
            text, meta = llm(prompt), {}
        u = self.usage.setdefault(kind, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        u["calls"] += 1
        u["input_tokens"] += meta.get("input_tokens", len(prompt) // 4)   # estimate when the model doesn't report
        u["output_tokens"] += meta.get("output_tokens", len(text) // 4)
        return text

    def plan(self, case):
        text = self.ask(PLAN + case, "plan")
        start = text.find("{")
        if start < 0:
            raise ValueError(f"the LLM returned no plan: {text[:200]!r}")
        data, _ = json.JSONDecoder().raw_decode(text[start:])      # the first JSON object; ignore any text after it
        steps = [s for s in (data.get("steps", []) if isinstance(data, dict) else data) if isinstance(s, dict)]
        split = []
        for s in steps:     # a check of several quoted texts at once ('"$360" with "*includes tax"'): one per text
            parts = re.findall(r"[\"“](.+?)[\"”]", str(s.get("value") or ""))
            if (s.get("op") or "").startswith("verify_text") and len(parts) > 1:
                split += [dict(s, value=x) for x in parts]
            else:
                split.append(s)
        steps = split
        written = [m[1].strip() for m in re.finditer(r"^\s*\d+[.)]\s+(.+)$", case, re.M)]
        for s in steps:     # a model that wrote the step as a call ("click(target=Cart link)"): make it words
            if re.match(r"^\s*\w+\s*\(.*\)\s*$", str(s.get("step") or "")):
                s["step"] = " ".join(str(x) for x in (s.get("op"), s.get("target"), s.get("value")) if x)
        for i, s in enumerate(steps):   # the step sentence is the test case's own when the counts agree
            if len(written) == len(steps):
                s["step"] = written[i]
            elif not s.get("step"):
                s["step"] = " ".join(str(x) for x in (s.get("op"), s.get("target"), s.get("value")) if x)
        for s in steps:     # values must come from the test case: repair copies the model garbled
            text = s.get("step", "")
            src = text if text and text in case else case    # the model's own wording: check values against the case
            if s.get("op") == "open" and not re.search(r"https?://", str(s.get("value") or "") + text):
                s["op"], s["target"] = "click", s.get("target") or s.get("value") or text     # "open the cart"
            if (s.get("op") or "").startswith("verify") and not s.get("value") and s.get("op") != "verify_url":
                quoted = re.search(r"[\"“](.+?)[\"”]", text)    # the model left the value out: take it from the step
                s["value"] = quoted[1] if quoted else (_respan(s["target"], src) if s.get("target") else None)
            v = s.get("value")
            if s.get("op") == "verify_text" and ORDINAL.search(text):     # "the first product is X": position matters
                s["op"], s["target"] = "verify_value", s.get("target") or text
            url = re.search(r"https?://[^\s\"'|()<>]+", text) or re.search(r"https?://[^\s\"'|()<>]+", case)
            if s.get("op") == "open" and url:
                s["value"] = url[0].rstrip(".,")
            elif isinstance(v, str) and v:
                quoted = re.search(r"[\"“](.+?)[\"”]", text)
                if quoted and v.lower() in quoted[1].lower():      # a cut-off copy of the quoted message
                    s["value"] = quoted[1]
                elif v.lower() not in src.lower():
                    s["value"] = quoted[1] if quoted else _respan(v, src)
        verbs = {"click": "Click", "type": "Type in", "select": "Select in", "check": "Check", "uncheck": "Uncheck",
                 "verify_value": "Check the value of"}
        shared = {t for t in (s.get("step") for s in steps) if sum(x.get("step") == t for x in steps) > 1}
        for s in steps:     # what to rank the page by: the step, or its own target when one sentence made several steps
            q = s.get("step", "")
            if q in shared and s.get("target"):
                q = f"{verbs.get(s.get('op'), '')} {s['target']}".strip()
            s["query"] = ANY.sub("the first", q)     # "add any phone" = the first one listed
        return steps

    # ---------- choosing an element ----------
    @staticmethod
    def exact_title(page, step, hits, target=None):
        """The candidate whose own title (card heading, label or name) is what the step acts on: the planned target
        minus generic words ("product Claw Hammer" -> "Claw Hammer"), or, without a target, a whole phrase of the
        step; the longest when several match. None when nothing matches, or two different elements share the title.
        "delete button for Claw Hammer" names a button, not Claw Hammer itself, so the product's cell is not taken."""
        core = None
        if target:
            core = re.sub(r"\b(the|a|an|product|item|link|button|card|tile|entry|option|checkbox|filter|field)\b", " ",
                          target, flags=re.I)
            core = re.sub(r"\s+", " ", core).strip().lower()
        found = []
        for h in hits:
            try:
                t = to_locator(page, h).evaluate(TITLE_JS)
            except Exception:  # noqa: S112 - stale or hidden candidate
                continue
            if not t or len(t) > 80:
                continue
            if core is not None and re.sub(r"\s+", " ", t).strip().lower() != core:
                continue
            if re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", step, re.I):
                found.append((len(t), -h["rank"], h))
        if not found:
            return None
        found.sort(key=lambda x: (x[0], x[1]), reverse=True)
        best = found[0]
        for other in found[1:]:
            if other[0] != best[0]:
                break
            try:    # same title: fine when it is the same thing (a heading inside its card link)
                a, b = to_locator(page, best[2]).element_handle(), to_locator(page, other[2]).element_handle()
                same = page.evaluate("([a, b]) => a.contains(b) || b.contains(a)", [a, b])
            except Exception:
                same = False
            if not same:
                return None
        return best[2]

    def pick(self, page, s, op, exclude=(), note=""):
        """The element for a step: the library's top match when confident, else the LLM's choice from the shortlist."""
        hits = [h for h in self.loc.find(page, s.get("query") or s["step"], max=8) if h["locator"] not in exclude]
        if op in FIT:   # typing needs a text box, check a checkbox …: only elements that can take the action
            hits = [h for h in hits if h["role"] in FIT[op]]
        if not hits:
            return None, "none"
        # a named thing to act on ("Claw Hammer"); for checks the name is the expected value, not the element
        names = re.findall(r"\b[A-Z][\w-]*", s.get("target") or "") if op in ACTION else []
        if names and not note and not any(all(n.lower() in h["doc"].lower() for n in names) for h in hits):
            return None, "none"     # it is not on the page (yet): wait / open something first, don't guess
        if not self.strict and not note:
            exact = self.exact_title(page, s.get("query") or s["step"], hits[:5], s.get("target"))
            if exact is not None:
                return exact, "library"
            sc = [h["score"] for h in hits]
            fits = op not in FIT or hits[0]["role"] in FIT[op]
            if names and not all(n.lower() in hits[0]["doc"].lower() for n in names):
                fits = False    # the top match is not the thing the step names ("Hammer" filter for "Claw Hammer")
            if fits and hits[0]["matches"] == 1 and (len(sc) == 1 or sc[0] - sc[1] >= self.margin):
                return hits[0], "library"
        cands = "\n".join(f"{i}. {h['doc']}" for i, h in enumerate(hits, 1))
        m = re.search(r"\d+", self.ask(CHOOSE.format(step=s.get("query") or s["step"], cands=cands, note=note),
                                       "choose"))
        k = int(m[0]) if m else 0
        return (hits[k - 1] if 0 < k <= len(hits) else None), "llm"

    def code_loc(self, page, hit, step):
        """(JavaScript locator for the script, live Locator): stable when the recorded one is volatile or positional."""
        lv = to_locator(page, hit)
        js, _, stable = stable_for(page, lv, hit["locator"], step)
        return js, stable or lv

    # ---------- running ----------
    def _until(self, cond):
        t0 = time.time()
        while True:
            try:
                if cond():
                    return True
            except Exception:  # noqa: S110 - page still changing: try again
                pass
            if time.time() - t0 > self.wait:
                return False
            time.sleep(0.3)

    @staticmethod
    def _settle(page):
        """Wait for the page's data, without hanging on sites that never stop loading (polling, ads, analytics)."""
        for state, ms in (("load", 15000), ("networkidle", 5000)):
            try:
                page.wait_for_load_state(state, timeout=ms)
            except Exception:  # noqa: S112 - still loading something forever: go on
                continue

    def _marks(self, page):
        try:
            return page.evaluate(MARKS_JS)
        except Exception:
            return {}

    def step(self, page, s, lines):
        """Run one planned step; returns (ok, info, who chose the element)."""
        op, v = s.get("op"), s.get("value")
        if op == "open":
            page.goto(v, wait_until="domcontentloaded")
            self._settle(page)
            lines.append(f"await page.goto({_q(v)});")
            lines.append("await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});")
            return True, "opened", "-"
        if op in ("back", "press"):
            page.go_back(wait_until="domcontentloaded") if op == "back" else page.keyboard.press(v or "Enter")
            self._settle(page)
            lines.append("await page.goBack();" if op == "back" else f"await page.keyboard.press({_q(v or 'Enter')});")
            if op == "back":
                lines.append(f"await expect(page).toHaveTitle({_q(page.title())});")
            return True, op, "-"
        if op in ("verify_text", "verify_absent", "verify_url") and not v:
            return False, "the plan has no expected value for this check", "-"
        if op == "verify_text":
            ok = self._until(lambda: page.get_by_text(v).filter(visible=True).count() > 0)
            lines.append(f"await expect(page.getByText({_q(v)}).filter({{ visible: true }}).first()).toBeVisible();")
            if not ok:      # text split across elements ("$360 <small>*includes tax</small>"): the page's own text
                flat = lambda t: re.sub(r"\s+", " ", t).strip().lower()     # noqa: E731
                try:
                    ok = flat(v) in flat(page.locator("body").inner_text())
                except Exception:
                    ok = False
                if ok:
                    lines[-1] = f"await expect(page.locator('body')).toContainText({_q(v)});"
                    return True, "visible (across elements)", "-"
            if not ok and any(v.lower() in m.lower() for m in self.dialogs):   # a browser popup said it
                lines[-1] = f"await expect.poll(() => dialogs.join(' | ')).toContain({_q(v)});"
                return True, "shown in a browser popup", "-"
            if not ok and v.lower() in self.last_change.lower():   # a toast that already faded: the action showed it
                return True, "appeared after the previous action (transient message)", "-"
            return ok, "visible" if ok else f"text not visible: {v}", "-"
        if op == "verify_absent":
            ok = self._until(lambda: page.get_by_text(v).filter(visible=True).count() == 0)
            lines.append(f"await expect(page.getByText({_q(v)}).filter({{ visible: true }})).toHaveCount(0);")
            return ok, "absent" if ok else f"text still shown: {v}", "-"
        if op == "verify_url":
            ok = self._until(lambda: v in page.url)
            lines.append(f"await expect(page).toHaveURL(new RegExp({_q(re.escape(v))}));")
            return ok, "url ok" if ok else f"url is {page.url}", "-"
        if op == "verify_value":
            return self._verify_value(page, s, str(v or ""), lines)
        action = ACTION.get(op)
        if not action:
            return False, f"unknown step type {op!r}", "-"
        if op == "select" and not self._has_fit(page, s, op):    # "select 12 as month" in a plain text field
            op, action = "type", "fill"
        hit, who = self.pick(page, s, op)
        for _ in range(3):      # the page may still be rendering (after back / navigation): look again
            if hit:
                break
            page.wait_for_timeout(1500)
            hit, who = self.pick(page, s, op)
        for _ in range(2):      # still nothing: the element is behind a dialog, menu or another page; open it first
            if hit:
                break
            if not self._reach(page, s, lines):
                break
            hit, who = self.pick(page, s, op)
        tried = []
        while hit:
            js, lv = self.code_loc(page, hit, s["step"])
            m0 = self._marks(page)
            res = act(page, lv, action, v, dialog="accept")      # alerts / confirms are accepted, like a user would
            self.last_change = res
            self.dialogs += [m[2] for m in re.finditer(r"A browser dialog appeared: (['\"])(.*?)\1 \(it was", res)]
            changed = "No change detected" not in res or "dialog appeared" in res or "new tab opened" in res
            if action != "click" or changed or tried:
                lines.append(f"await {js}{CODE[action].format(v=_q(v))};")
                self._sync(page, res, m0, lines)
                return True, res[:300], who
            tried.append(hit["locator"])     # clicked, nothing changed: let the LLM look at the shortlist again
            hit, who = self.pick(page, s, op, exclude=tried,
                                 note=f"Clicking {hit['doc']} changed nothing on the page.\n")
        return False, "no matching element", who

    def _has_fit(self, page, s, op):
        return any(h["role"] in FIT[op] for h in self.loc.find(page, s.get("query") or s["step"], max=8))

    def _reach(self, page, s, lines):
        """Written test cases skip obvious steps ("Login with user X" never says "open the login dialog"). Ask the LLM
        which one element to click to get to the step's element, click it and record it. True if something opened."""
        hits = self.loc.find(page, s.get("query") or s["step"], max=10)
        if not hits:
            return False
        cands = "\n".join(f"{i}. {h['doc']}" for i, h in enumerate(hits, 1))
        txt = self.ask(REACH.format(step=s["step"], cands=cands), "choose")
        m = re.search(r"\d+", txt)
        k = int(m[0]) if m else 0
        if not 0 < k <= len(hits):
            return False
        js, lv = self.code_loc(page, hits[k - 1], s["step"])
        m0 = self._marks(page)
        res = act(page, lv, "click", dialog="accept")
        if "No change detected" in res and "dialog appeared" not in res:
            return False
        self.last_change = res
        self.dialogs += [m[2] for m in re.finditer(r"A browser dialog appeared: (['\"])(.*?)\1 \(it was", res)]
        lines.append(f"await {js}.click();")
        self._sync(page, res, m0, lines)
        return True

    def _sync(self, page, res, m0, lines):
        """Make the script wait for what the action changed, so it never races ahead of the app."""
        if "dialog appeared" in res:    # e.g. "Product added" after the server answered: wait for that popup
            lines.append(f"await expect.poll(() => dialogs.length).toBeGreaterThanOrEqual({len(self.dialogs)});")
        if res.startswith("Navigated"):
            t = page.title()
            if t and not VOLATILE.search(t):
                lines.append(f"await expect(page).toHaveTitle({_q(t)});")
        elif "No change detected" not in res:
            m1 = self._marks(page)
            changed = sorted(((k, t) for k, t in m1.items() if m0.get(k) != t and not VOLATILE.search(k)),
                             key=lambda x: len(x[1]))     # the innermost / shortest: the badge, not the whole link
            for k, t in changed[:1]:
                attr, val = k.split("=", 1)
                lines.append(f"await expect(page.locator({_q(f'[{attr}={_q(val)}]')})).toHaveText({_q(t)});")

    def _verify_value(self, page, s, v, lines):
        hit, who = self.pick(page, s, "verify")
        if not hit:     # values that load late (a cart total filled in by a request): look once more
            page.wait_for_timeout(2000)
            hit, who = self.pick(page, s, "verify")
        for attempt in range(2):
            if not hit:
                return False, "element not found", who
            js, lv = self.code_loc(page, hit, s["step"])
            is_input = lv.evaluate("e => ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.tagName)")
            got = lv.input_value() if is_input else lv.inner_text()
            ok = got.strip() == v.strip() if is_input else v.lower() in got.lower()
            label_only = not re.search(r"\d", got) and re.search(r"\d", v)
            if not ok and not is_input and label_only:     # a label ("Total") whose value sits next to it
                for depth in (1, 2):
                    up = "/".join([".."] * depth)
                    anc = lv.locator(f"xpath={up}")
                    try:
                        text = anc.inner_text()
                        if len(text) <= 200 and v.lower() in text.lower():
                            lines.append(f"await expect({js}.locator('xpath={up}')).toContainText({_q(v)});")
                            return True, f"shows {v!r} next to {got.strip()!r}", who
                    except Exception:  # noqa: S112 - no such ancestor
                        continue
            if ok or attempt:
                lines.append(f"await expect({js}).{'toHaveValue' if is_input else 'toContainText'}({_q(v)});")
                return ok, f"shows {got!r}", who
            # mismatch: let the LLM check it read the right element before the step fails
            hit, who = self.pick(page, s, "verify", exclude=[hit["locator"]],
                                 note=f"The element {hit['doc']} shows {got!r}, not {v!r}; pick the element the step "
                                      "means.\n")
        return False, "mismatch", who

    def run(self, page, case, steps=None):
        """Run a manual test case (plain text) on a sync Playwright page. steps: an existing plan to reuse."""
        self.usage, self.last_change, self.dialogs = {}, "", []
        steps = steps or self.plan(case)
        lines, log = [], []
        for s in steps:
            try:
                ok, info, who = self.step(page, s, lines)
            except Exception as e:
                ok, info, who = False, f"error: {str(e)[:200]}", "-"
            log.append(dict(step=s.get("step"), op=s.get("op"), ok=ok, info=info, chosen_by=who))
            if not ok and not (s.get("op") or "").startswith("verify"):
                break   # an action failed: later steps would run on the wrong page
        return Result(steps, log, lines, self.usage, list(self.dialogs))
