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
(a message, a name, a price such as $28.30), never words that say where it is ("line total", "the cart shows").
Example: {"steps": [{"step": "Type pliers in the search box", "op": "type", "target": "search box", "value": "pliers"},
{"step": "Verify the product Thor Hammer is not listed", "op": "verify_absent", "value": "Thor Hammer"}]}
Answer with JSON only.

Test case:
"""
CHOOSE = """Test step: {step}
Candidate elements on the page:
{cands}
{note}Reply with only the number of the element this step acts on, or 0 if none fits."""

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

    @property
    def passed(self):
        return bool(self.log) and all(x["ok"] for x in self.log)

    def spec(self, title="test"):
        """The Playwright test (JavaScript) for this run."""
        body = "\n".join("  " + x for x in self.lines)
        return (f"import {{ test, expect }} from '@playwright/test';\n\n"
                f"test({_q(title)}, async ({{ page }}) => {{\n{body}\n}});\n")


class TestRunner:
    __test__ = False    # not a pytest test class

    def __init__(self, llm, locator=None, margin=1.0, strict=False, wait=5.0, planner=None):
        """llm: a LangChain chat model (anything with .invoke returning a message) or a function prompt -> text.
        planner: a different model for the one planning call per test case (e.g. the same model in JSON mode).
        margin: how far (fused z-score units) the top match must lead the second to act without the LLM.
        strict: the LLM chooses every element (for re-running a failed case)."""
        self.llm, self.loc, self.margin, self.strict, self.wait = llm, locator or Locator(), margin, strict, wait
        self.planner, self.usage, self.last_change = planner or llm, {}, ""

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
        written = [m[1].strip() for m in re.finditer(r"^\s*\d+[.)]\s+(.+)$", case, re.M)]
        for i, s in enumerate(steps):   # the step sentence is the test case's own when the counts agree
            if len(written) == len(steps):
                s["step"] = written[i]
            elif not s.get("step"):
                s["step"] = " ".join(str(x) for x in (s.get("op"), s.get("target"), s.get("value")) if x)
        for s in steps:     # values must come from the test case: repair copies the model garbled
            text = s.get("step", "")
            if (s.get("op") or "").startswith("verify") and not s.get("value") and s.get("op") != "verify_url":
                quoted = re.search(r"[\"“](.+?)[\"”]", text)    # the model left the value out: take it from the step
                s["value"] = quoted[1] if quoted else (_respan(s["target"], text) if s.get("target") else None)
            v = s.get("value")
            if s.get("op") == "verify_text" and ORDINAL.search(text):     # "the first product is X": position matters
                s["op"], s["target"] = "verify_value", s.get("target") or text
            url = re.search(r"https?://\S+", text)
            if s.get("op") == "open" and url:
                s["value"] = url[0].rstrip(".,")
            elif isinstance(v, str) and v:
                quoted = re.search(r"[\"“](.+?)[\"”]", text)
                if quoted and v.lower() in quoted[1].lower():      # a cut-off copy of the quoted message
                    s["value"] = quoted[1]
                elif v.lower() not in text.lower():
                    s["value"] = quoted[1] if quoted else _respan(v, text)
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
        hits = [h for h in self.loc.find(page, s["step"], max=8) if h["locator"] not in exclude]
        if not hits:
            return None, "none"
        if not self.strict and not note:
            exact = self.exact_title(page, s["step"], hits[:5], s.get("target"))
            if exact is not None:
                return exact, "library"
            sc = [h["score"] for h in hits]
            fits = op not in FIT or hits[0]["role"] in FIT[op]
            if fits and hits[0]["matches"] == 1 and (len(sc) == 1 or sc[0] - sc[1] >= self.margin):
                return hits[0], "library"
        cands = "\n".join(f"{i}. {h['doc']}" for i, h in enumerate(hits, 1))
        m = re.search(r"\d+", self.ask(CHOOSE.format(step=s["step"], cands=cands, note=note), "choose"))
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

    def _marks(self, page):
        try:
            return page.evaluate(MARKS_JS)
        except Exception:
            return {}

    def step(self, page, s, lines):
        """Run one planned step; returns (ok, info, who chose the element)."""
        op, v = s.get("op"), s.get("value")
        if op == "open":
            page.goto(v)
            page.wait_for_load_state("networkidle")
            lines.append(f"await page.goto({_q(v)});")
            lines.append("await page.waitForLoadState('networkidle');")   # app data loaded before the first step
            return True, "opened", "-"
        if op in ("back", "press"):
            page.go_back() if op == "back" else page.keyboard.press(v or "Enter")
            page.wait_for_load_state("networkidle")
            lines.append("await page.goBack();" if op == "back" else f"await page.keyboard.press({_q(v or 'Enter')});")
            if op == "back":
                lines.append(f"await expect(page).toHaveTitle({_q(page.title())});")
            return True, op, "-"
        if op in ("verify_text", "verify_absent", "verify_url") and not v:
            return False, "the plan has no expected value for this check", "-"
        if op == "verify_text":
            ok = self._until(lambda: page.get_by_text(v).filter(visible=True).count() > 0)
            lines.append(f"await expect(page.getByText({_q(v)}).filter({{ visible: true }}).first()).toBeVisible();")
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
        hit, who = self.pick(page, s, op)
        if not hit:     # the page may still be rendering (after back / navigation): look once more
            page.wait_for_timeout(1500)
            hit, who = self.pick(page, s, op)
        tried = []
        while hit:
            js, lv = self.code_loc(page, hit, s["step"])
            m0 = self._marks(page)
            res = act(page, lv, action, v)
            self.last_change = res
            if action != "click" or "No change detected" not in res or tried:
                lines.append(f"await {js}{CODE[action].format(v=_q(v))};")
                self._sync(page, res, m0, lines)
                return True, res[:300], who
            tried.append(hit["locator"])     # clicked, nothing changed: let the LLM look at the shortlist again
            hit, who = self.pick(page, s, op, exclude=tried,
                                 note=f"Clicking {hit['doc']} changed nothing on the page.\n")
        return False, "no matching element", who

    def _sync(self, page, res, m0, lines):
        """Make the script wait for what the action changed, so it never races ahead of the app."""
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
        self.usage, self.last_change = {}, ""
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
        return Result(steps, log, lines, self.usage)
