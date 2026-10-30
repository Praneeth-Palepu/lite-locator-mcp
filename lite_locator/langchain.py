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

from elem_rank.diff import Snap, outcome, split_snapshot
from elem_rank.locate import Locator

ACTIONS = {"browser_click", "browser_type", "browser_fill_form", "browser_select_option", "browser_hover",
           "browser_press_key", "browser_drag", "browser_navigate", "browser_navigate_back", "browser_navigate_forward",
           "browser_handle_dialog", "browser_file_upload", "browser_wait_for", "browser_tab_select"}

LOCATOR_HINT = ("To locate an element for a step, call browser_find_element with the step in plain words and use the "
                "best fitting candidate. Call browser_snapshot only if no candidate fits.\n")

DESC = ("Find the page elements that match a test step, without reading the whole page. Give the step in plain words "
        "(e.g. 'Enter standard_user in the username field', 'Click Buy Now for the Halloween package'). Returns a short "
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


def with_lite_locator(tools, locator=None, lam=0.01, name="browser_find_element", diffs=True):
    """Your Playwright MCP tools plus browser_find_element, which ranks the page's own snapshot (taken with your
    existing browser_snapshot tool) and returns only the best few elements with their refs.
    diffs: action tools return what changed instead of a full snapshot (False = unchanged tools)."""
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

    def run(text, step, max):
        if "[ref=" not in text:  # no page open or an error message: pass it through
            return text or "No page open."
        remember(text)
        hits = loc.find(text, step, max=max)
        # keep only the page URL/title lines above the tree (the layout differs between Playwright MCP versions)
        head = "\n".join(x for x in text.split("```yaml", 1)[0].splitlines() if "Page URL:" in x or "Page Title:" in x)
        return _report(hits, step, head)

    def find(step: str, max: int | None = None) -> str:
        return run(_text(snap.invoke({})), step, max)

    async def afind(step: str, max: int | None = None) -> str:
        return await asyncio.to_thread(run, _text(await snap.ainvoke({})), step, max)

    tool = StructuredTool.from_function(
        func=find, coroutine=afind, name=name,
        description=DESC.format(use="Pass a candidate's ref to browser_click, browser_type etc. (the `ref`/`target` "
                                    "argument your Playwright tools take).",
                                fallback="call browser_snapshot to see the full page."))
    def result(orig_name, text, after_text):
        rest, yaml, url, title = split_snapshot(text)
        after = Snap(url, title, yaml) if yaml else _state(after_text or "")
        if after is None:
            return text
        before, seen["last"] = seen["last"], after
        return f"{rest}\n\n### Result\n{outcome(before, after)}".strip()

    def wrap(t):
        if t.name.endswith("browser_snapshot"):  # full tree on request: pass through, remember it
            async def acall(**kw):
                out = _text(await t.ainvoke(kw))
                remember(out)
                return out

            def call(**kw):
                out = _text(t.invoke(kw))
                remember(out)
                return out
        elif t.name.split("__")[-1] in ACTIONS:
            async def acall(**kw):
                out = _text(await t.ainvoke(kw))
                return result(t.name, out, None if "```yaml" in out else _text(await snap.ainvoke({})))

            def call(**kw):
                out = _text(t.invoke(kw))
                return result(t.name, out, None if "```yaml" in out else _text(snap.invoke({})))
        else:
            return t
        desc = t.description + ("" if t.name.endswith("browser_snapshot") else
                                " Returns what changed on the page (navigation, added/removed elements, or no change), "
                                "not a full snapshot.")
        return StructuredTool(name=t.name, description=desc, args_schema=t.args_schema, func=call, coroutine=acall)

    others = [t for t in tools if t.name != name]
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
