"""LangChain / LangGraph tools around the Locator (needs langchain-core, which your agents already have).

Playwright MCP through langchain-mcp-adapters (works with any mcp version, the tool calls your browser_snapshot):
    tools = await client.get_tools()
    tools = with_lite_locator(tools)            # adds browser_find_element, keeps every existing tool
    agent = create_react_agent(llm, tools, prompt=LOCATOR_HINT + your_prompt)

Actions (browser_click, browser_type, …) then return what changed on the page instead of a full snapshot:
"Navigated to …" + a short summary, "Same page. Changes: + alert …", or "No visible change". browser_snapshot still
returns the full tree (the fallback). For older history, `snapshot_hook` drops full snapshots that later steps
superseded:  create_react_agent(llm, tools, pre_model_hook=snapshot_hook)

Playwright Python page (sync or async) instead of MCP:
    tools = [page_tool(page)]                   # returns candidates with ready Playwright locators
"""
import asyncio
import inspect
import json
import time

from elem_rank.diff import Snap, noise, outcome, split_snapshot
from elem_rank.locate import POLL, WAIT_MAX
from elem_rank.locate import Locator
from elem_rank.snapshot import DETAILS_JS, elements, needs_details
from elem_rank.steps import clean_args

ACTIONS = {"browser_click", "browser_type", "browser_fill_form", "browser_select_option", "browser_hover",
           "browser_press_key", "browser_drag", "browser_navigate", "browser_navigate_back", "browser_navigate_forward",
           "browser_handle_dialog", "browser_file_upload", "browser_wait_for", "browser_tab_select"}

LOCATOR_HINT = ("To locate an element for a step, call browser_find_element with the step in plain words and use the "
                "best fitting candidate; pass only its ref (e.g. e54) to the action tool. Call browser_snapshot only if "
                "no candidate fits, and for checks that need the whole page or a whole list (counts, 'all items', "
                "totals, comparing many values). After an action, read its Result: if it says no change was "
                "detected, do not repeat a submit, payment or delete; check the page with browser_snapshot first.\n")

DESC = ("Find the page elements that match a test step, without reading the whole page. Give the step in plain words "
        "(e.g. 'Enter standard_user in the username field', 'Click Buy Now for the Halloween package'); describing the "
        "element to act on works best ('the search box', 'the quantity field'). Returns a short "
        "list of candidates, best first. {use} If none of the candidates fits the step, {fallback}")


def _text(out):
    """Tool output as text, whatever shape the adapter version returns (str, content blocks, (content, artifact))."""
    if isinstance(out, tuple):
        out = out[0]
    if isinstance(out, str):
        return out
    if isinstance(out, list):
        return "".join(x.get("text", "") if isinstance(x, dict) else getattr(x, "text", str(x)) for x in out)
    return getattr(out, "content", None) and _text(out.content) or str(out)


def _report(hits, step, header=""):
    out = ([header, ""] if header else []) + [f"### Candidates for: {step}", hits.prompt or "(no elements found)", "",
                                              f"({len(hits)} of {hits.total} elements, ~{hits.tokens} tokens instead of "
                                              f"~{hits.full_tokens} for the full page.)"]
    if hits.fallback_full_tree:
        out.append("Note: these candidates may be ambiguous; check the full page before acting.")
    return "\n".join(out)


def _state(text):
    _, yaml, url, title = split_snapshot(text)
    return Snap(url, title, yaml) if yaml else None


def with_lite_locator(tools, locator=None, lam=0.01, name="browser_find_element", diffs=True, keep=None):
    """Your Playwright MCP tools plus browser_find_element, which ranks the page's own snapshot (taken with your
    existing browser_snapshot tool) and returns only the best few elements with their refs.
    diffs: action tools return what changed instead of a full snapshot (False = unchanged tools).
    keep: names of the tools the LLM should see (browser_find_element is always added). Tool definitions are sent
    with every LLM call, so fewer tools = fewer tokens. Pass the FULL tool list as `tools`: the library itself uses
    browser_snapshot and browser_evaluate even when the LLM doesn't see them."""
    from langchain_core.tools import StructuredTool
    snap = next((t for t in tools if t.name.endswith("browser_snapshot")), None)
    if snap is None:
        raise ValueError("no browser_snapshot tool in `tools`: pass the Playwright MCP tools")
    loc = locator or Locator(lam)
    seen = {"last": None}  # the page as the LLM last saw it (snapshot, find or action)

    def remember(text):
        st = _state(text)
        if st:
            seen["last"] = st

    def run(text, step, max, details=None):
        if "[ref=" not in text:  # no page open or an error message: pass it through
            return text or "No page open."
        remember(text)
        hits = loc.find(text, step, max=max, details=details)
        # keep only the page URL/title lines above the tree (the layout differs between Playwright MCP versions)
        head = "\n".join(x for x in text.split("```yaml", 1)[0].splitlines() if "Page URL:" in x or "Page Title:" in x)
        return _report(hits, step, head)

    evaluate = next((t for t in tools if t.name.endswith("browser_evaluate")), None)
    cache = {}  # snapshot -> DOM details of its buttons / links (one browser_evaluate call per page state)

    def wanted(text):
        if evaluate is None or "[ref=" not in text or text in cache:
            return False
        return needs_details(elements(text))

    def parse(out):
        try:
            res = _text(out).split("### Result", 1)[-1].split("###", 1)[0].strip()
            return json.loads(json.loads(res)) if res.startswith('"') else json.loads(res)
        except (ValueError, TypeError):
            return None

    def find(step: str, max: int | None = None) -> str:
        text = _text(snap.invoke({}))
        if wanted(text):
            cache[text] = parse(evaluate.invoke({"function": DETAILS_JS}))
        return run(text, step, max, cache.get(text))

    async def afind(step: str, max: int | None = None) -> str:
        text = _text(await snap.ainvoke({}))
        if wanted(text):
            cache[text] = parse(await evaluate.ainvoke({"function": DETAILS_JS}))
        return await asyncio.to_thread(run, text, step, max, cache.get(text))

    tool = StructuredTool.from_function(
        func=find, coroutine=afind, name=name,
        description=DESC.format(use="Pass a candidate's ref to browser_click, browser_type etc. (the `ref`/`target` "
                                    "argument your Playwright tools take).",
                                fallback="call browser_snapshot to see the full page."))
    def finish(text, pre, quiet, t0, again):
        """Outcome of an action; if nothing changed yet, keep checking (slow responses) up to WAIT_MAX seconds."""
        rest, yaml, url, title = split_snapshot(text)
        after = Snap(url, title, yaml) if yaml else again()
        if after is None:
            return text
        while True:
            res = outcome(pre, after, quiet, time.time() - t0)
            if time.time() - t0 >= WAIT_MAX:
                break
            time.sleep(POLL)
            nxt = again() or after
            if "No change detected" not in res and nxt.snapshot == after.snapshot:
                break  # changed, and settled (lists re-rendered after a sort, results after a search)
            after = nxt
        seen["last"] = after
        return f"{rest}\n\n### Result\n{res}".strip()

    async def afinish(text, pre, quiet, t0, again):
        rest, yaml, url, title = split_snapshot(text)
        after = Snap(url, title, yaml) if yaml else await again()
        if after is None:
            return text
        while True:
            res = outcome(pre, after, quiet, time.time() - t0)
            if time.time() - t0 >= WAIT_MAX:
                break
            await asyncio.sleep(POLL)
            nxt = (await again()) or after
            if "No change detected" not in res and nxt.snapshot == after.snapshot:
                break
            after = nxt
        seen["last"] = after
        return f"{rest}\n\n### Result\n{res}".strip()

    def before_state(pre):
        """Positions that change by themselves (tickers): those since the last look, on the same page."""
        last = seen["last"]
        return noise(last.snapshot, pre.snapshot) if pre and last and last.url == pre.url else frozenset()

    def wrap(t):
        if t.name.endswith("browser_snapshot"):  # full tree on request: pass through (inline), remember it
            async def acall(**kw):
                out = _text(await t.ainvoke(clean_args(t.name, kw)))
                remember(out)
                return out

            def call(**kw):
                out = _text(t.invoke(clean_args(t.name, kw)))
                remember(out)
                return out
        elif t.name.split("__")[-1] in ACTIONS:
            async def again_a():
                return _state(_text(await snap.ainvoke({})))

            async def acall(**kw):
                pre = await again_a()
                quiet, t0 = before_state(pre), time.time()
                out = _text(await t.ainvoke(clean_args(t.name, kw)))
                return await afinish(out, pre or seen["last"], quiet, t0, again_a)

            def again_s():
                return _state(_text(snap.invoke({})))

            def call(**kw):
                pre = again_s()
                quiet, t0 = before_state(pre), time.time()
                out = _text(t.invoke(clean_args(t.name, kw)))
                return finish(out, pre or seen["last"], quiet, t0, again_s)
        else:
            return t
        desc = t.description + ("" if t.name.endswith("browser_snapshot") else
                                " Returns what changed on the page (navigation, added/removed elements, or no change), "
                                "not a full snapshot.")
        return StructuredTool(name=t.name, description=desc, args_schema=t.args_schema, func=call, coroutine=acall)

    others = [t for t in tools if t.name != name and (keep is None or t.name.split("__")[-1] in set(keep))]
    return [tool] + ([wrap(t) for t in others] if diffs else others)


def trim_snapshots(messages, keep=1):
    """Replace all but the last `keep` full snapshots in tool messages with a one-line stub: old trees are never
    needed again once the page has moved on, but every LLM call would pay for them."""
    idx = [i for i, m in enumerate(messages) if getattr(m, "type", "") == "tool" and "```yaml" in _text(m.content)]
    drop = set(idx[:-keep] if keep else idx)
    out = []
    for i, m in enumerate(messages):
        if i in drop:
            head = _text(m.content).split("```yaml", 1)[0].strip()
            m = m.model_copy(update={"content": head + "\n[full snapshot removed: superseded by later steps]"})
        out.append(m)
    return out


def snapshot_hook(state):
    """LangGraph pre_model_hook: the LLM sees the history without superseded snapshots (the stored history is kept)."""
    return {"llm_input_messages": trim_snapshots(state["messages"])}


def page_tool(page, locator=None, lam=0.01, name="find_element"):
    """A tool over a Playwright Python Page (sync or async): candidates with ready, live-checked Playwright locators."""
    from langchain_core.tools import StructuredTool
    loc = locator or Locator(lam)
    is_async = inspect.iscoroutinefunction(page.evaluate)
    desc = DESC.format(use="Each candidate comes with a Playwright locator, checked on the live page.",
                       fallback="say so; the full page will be provided.")

    if is_async:
        async def afind(step: str, max: int | None = None) -> str:
            return _report(await loc.afind(page, step, max=max), step)
        return StructuredTool.from_function(coroutine=afind, name=name, description=desc)

    def find(step: str, max: int | None = None) -> str:
        return _report(loc.find(page, step, max=max), step)
    return StructuredTool.from_function(func=find, name=name, description=desc)
