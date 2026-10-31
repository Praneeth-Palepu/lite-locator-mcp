"""Explore a website, let an LLM write test cases, and turn them into verified Playwright tests, with lite-locator
grounding every step on the live page (the LLM sees a few candidates per step, not the whole page).

    pip install lite-locator-mcp playwright langchain-core langchain-ollama    # or langchain-openai for Azure
    python explore_generate.py https://practicesoftwaretesting.com --pages 3 --cases 3 --out test_generated.py
    pytest test_generated.py            # needs pytest-playwright

Flow:
  1. explore   visit the start page and same-site links (breadth first, slowly), make a short outline of each page
  2. plan      the LLM reads an outline and writes test cases as JSON steps ("fill | the username field | standard_user")
  3. ground    for each step, Locator.find(page, step) shortlists elements; the LLM picks one by number (or 0 = none
               fits, then it gets the full element list: the fallback). Steps run live, so later pages are real.
  4. emit      every step that ran becomes a line of pytest-playwright code, with the locator that worked.

LLM: --llm ollama (local, default) | azure (AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT,
OPENAI_API_VERSION) | openai (OPENAI_API_KEY, --model).
"""
import argparse
import json
import re
import time
from urllib.parse import urljoin, urlparse

from playwright.sync_api import expect, sync_playwright

from lite_locator import Locator
from elem_rank.locate import JS, to_locator

ACTIONS = ("fill", "click", "select", "check", "assert_visible", "assert_text", "assert_url")

PLAN = """You are a QA engineer. Write {n} test cases for this web page: realistic user flows, positive and negative.
Each step has an action, a plain-words description of the target element, and a value when needed.
Actions: fill (value = text to type), click, select (value = option), check, assert_visible (target = what must be
shown, e.g. "the error message"), assert_text (value = exact text that must appear; only text you can see on the
page, otherwise use assert_visible), assert_url (value = a part of the expected URL, e.g. "inventory").
Steps may continue on the pages the flow navigates to. Use test data shown on the page when there is any.
Reply with JSON only: [{{"name": "...", "steps": [{{"action": "...", "target": "...", "value": "..."}}]}}]

Page: {url}  ({title})
{outline}"""

PICK = """Test step: {step}
Which element should the step act on? Candidates:
{cands}
Reply with the number only, or 0 if none of them fits."""


def make_llm(kind, model):
    if kind == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=model or "qwen3:4b-instruct", temperature=0, num_ctx=8192, num_predict=1024, keep_alive=-1)
    if kind == "azure":
        import os
        from langchain_openai import AzureChatOpenAI
        return AzureChatOpenAI(azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"], temperature=0)
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=model or "gpt-4o-mini", temperature=0)


class Meter:
    """Prompt tokens (about 4 characters each) per purpose, to see where they go."""

    def __init__(self):
        self.t = {"plan": 0, "pick": 0, "fallback_full_page": 0}

    def ask(self, llm, prompt, purpose):
        self.t[purpose] += len(prompt) // 4
        return llm.invoke(prompt).content.strip()


def elements(page):
    return page.evaluate(JS, 1500)["elements"]


def outline(page, cap=70):
    """Short page summary for planning: headings and interactive elements, one line each, deduplicated."""
    seen, lines = set(), []
    for e in elements(page):
        label = e.get("name") or e.get("text") or e.get("attrs", {}).get("placeholder") or ""
        if e["kind"] != "interactive" and e["role"] != "heading" or not label:
            continue
        key = (e["role"], label.lower())
        if key not in seen:
            seen.add(key)
            lines.append(f'- {e["role"]} "{label[:60]}"')
    return "\n".join(lines[:cap])


def explore(page, url, max_pages, delay):
    site, todo, pages = urlparse(url).netloc, [url], []
    while todo and len(pages) < max_pages:
        u = todo.pop(0)
        if any(p["url"] == u for p in pages):
            continue
        page.goto(u, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        pages.append({"url": page.url, "title": page.title(), "outline": outline(page)})
        for href in page.eval_on_selector_all("a[href]", "els => els.map(a => a.getAttribute('href'))"):
            v = urljoin(page.url, href).split("#")[0]
            if urlparse(v).netloc == site and v not in todo and v.startswith("http"):
                todo.append(v)
        time.sleep(delay)  # go slowly on sites you do not own
    return pages


def parse_json(text):
    m = re.search(r"\[.*\]", text, re.S)
    return json.loads(m.group(0)) if m else []


def ground(page, loc, llm, meter, step):
    """Pick the element for a step: shortlist first, the full element list only when nothing in it fits."""
    hits = loc.find(page, step)
    if hits:
        cands = "\n".join(f'{h["rank"]}. {h["doc"]}' for h in hits)
        k = re.findall(r"\d+", meter.ask(llm, PICK.format(step=step, cands=cands), "pick"))
        k = int(k[0]) if k else 0
        if 1 <= k <= len(hits):
            return hits[k - 1], "shortlist"
    # fallback: the whole page, as the LLM would see it without the library
    full = loc.find(page, step, max=10_000)
    cands = "\n".join(f'{h["rank"]}. {h["doc"]}' for h in full)
    k = re.findall(r"\d+", meter.ask(llm, PICK.format(step=step, cands=cands), "fallback_full_page"))
    k = int(k[0]) if k else 0
    return (full[k - 1], "full page") if 1 <= k <= len(full) else (None, "not found")


def run_case(page, loc, llm, meter, url, case):
    """Run a test case live; return the code lines that worked and the result."""
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(1000)
    code = [f'    page.goto("{url}")']
    for st in case.get("steps", []):
        act, target, value = st.get("action"), st.get("target", ""), str(st.get("value", ""))
        try:
            if act == "assert_url":
                part = urlparse(value or target).path.strip("/") or value or target
                expect(page).to_have_url(re.compile(re.escape(part)))
                code.append(f"    expect(page).to_have_url(re.compile({re.escape(part)!r}))")
                continue
            if act == "assert_text":  # exact text from the test case: no ranking needed, any language
                try:
                    expect(page.get_by_text(value).first).to_be_visible(timeout=3000)
                    code.append(f"    expect(page.get_by_text({value!r}).first).to_be_visible()")
                    continue
                except AssertionError:  # the planner guessed the wording: check the element it described instead
                    act = "assert_visible"
                    code.append(f"    # exact text {value!r} not on the page; checking '{target}' instead")
            if act not in ACTIONS:
                return code, f"skipped unknown action {act!r}"
            hit, how = ground(page, loc, llm, meter, f"{act} {target}" + (f" with {value}" if value else ""))
            if hit is None:
                return code, f"no element for: {act} {target}"
            el, py = to_locator(page, hit), hit["py"]  # live Locator for running, Python text for the test file
            line = {"fill": f".fill({value!r})", "click": ".click()", "select": f".select_option(label={value!r})",
                    "check": ".check()", "assert_visible": None}[act]
            if line is None:
                expect(el).to_be_visible()
                code.append(f"    expect({py}).to_be_visible()  # {how}")
            else:
                if act == "fill":
                    el.fill(value)
                elif act == "select":
                    el.select_option(label=value)
                else:
                    getattr(el, act)()  # click / check
                code.append(f"    {py}{line}  # {how}")
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(600)
        except Exception as ex:
            return code, f"failed at '{act} {target}': {str(ex).splitlines()[0][:120]}"
    return code, "passed"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--pages", type=int, default=2, help="pages to explore")
    ap.add_argument("--cases", type=int, default=3, help="test cases per page")
    ap.add_argument("--llm", default="ollama", choices=["ollama", "azure", "openai"])
    ap.add_argument("--model")
    ap.add_argument("--delay", type=float, default=5, help="seconds between page loads while exploring")
    ap.add_argument("--out", default="test_generated.py")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()

    llm, loc, meter = make_llm(a.llm, a.model), Locator(), Meter()
    tests, report = [], []
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=not a.headed)
        page = b.new_page(viewport={"width": 1366, "height": 900})
        for p in explore(page, a.url, a.pages, a.delay):
            print(f"\n== {p['url']}  ({p['title']})")
            cases = parse_json(meter.ask(llm, PLAN.format(n=a.cases, **p), "plan"))
            for c in cases:
                code, result = run_case(page, loc, llm, meter, p["url"], c)
                print(f"  [{result}] {c.get('name')}")
                name = re.sub(r"\W+", "_", c.get("name", "case").lower()).strip("_")[:60]
                status = "" if result == "passed" else f"    # NOTE: {result}\n"
                tests.append(f"def test_{name}(page: Page):\n{status}" + "\n".join(code) + "\n")
                report.append({"page": p["url"], "case": c.get("name"), "result": result})
        b.close()

    header = "import re\n\nfrom playwright.sync_api import Page, expect\n\n\n"
    open(a.out, "w").write(header + "\n\n".join(tests))
    print(f"\nwrote {a.out}: {len(tests)} tests, {sum(r['result'] == 'passed' for r in report)} passed while generating")
    print("prompt tokens:", meter.t)


if __name__ == "__main__":
    main()
