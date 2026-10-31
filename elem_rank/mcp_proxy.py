"""lite-locator-mcp: Playwright MCP with a semantic element finder in front, so the LLM sees a shortlist instead of
the whole accessibility tree.

The proxy starts Playwright MCP as a child process (same browser for every call) and exposes all of its tools
unchanged, plus one more:

  browser_find_element(step)  takes the snapshot inside the proxy, ranks the elements with our models (fusion
                              retriever + reranker), lets the RL cut policy choose how many to show, and returns
                              only those lines with their refs. The LLM then acts with the normal tools, e.g.
                              browser_click(ref=...). browser_snapshot stays available as the fallback.

Actions (click, type, navigate, …) return what changed on the page instead of a full snapshot: a navigation
summary, the added/removed/changed elements, or "No visible change" (--full-snapshots turns this off).
Playwright MCP runs exactly as configured. Optional --lean adds --snapshot-mode none, so clicks and typing no longer
append a full snapshot to their results (the tokens saved on finding are otherwise spent again on every action);
it changes what the agent sees after each action and needs a Playwright MCP version that supports the flag.

Usage (stdio MCP server):
  lite-locator-mcp [--lam 0.01] [--lean] [-- <extra Playwright MCP args>]
Env: ELEM_RANK_HOME (folder with models/), PLAYWRIGHT_MCP (default "npx -y @playwright/mcp@0.0.83").
"""
import argparse
import asyncio
import json
import os
import shlex
import sys
import time

from importlib.metadata import PackageNotFoundError, version

try:
    if int(version("mcp").split(".")[0]) < 2:
        raise ImportError("mcp " + version("mcp"))
    from mcp import ClientSession, StdioServerParameters, types
    from mcp.client.stdio import stdio_client
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server
except (ImportError, PackageNotFoundError):  # the library API needs no MCP (any version); only this proxy does
    sys.exit("lite-locator-mcp needs the MCP extra (mcp>=2.3): pip install 'lite-locator-mcp[mcp]'\n"
             "Keep an older mcp untouched by installing it in its own venv. "
             "For use inside your own agents: from lite_locator import Locator")

import numpy as np

from .diff import Snap, noise, outcome, split_snapshot
from .locate import POLL, WAIT_MAX
from .snapshot import DETAILS_JS, demote_orphans, elements, line, merge_details, needs_details
from .steps import clean_args

ACTIONS = {"browser_click", "browser_type", "browser_fill_form", "browser_select_option", "browser_hover",
           "browser_press_key", "browser_drag", "browser_navigate", "browser_navigate_back", "browser_navigate_forward",
           "browser_handle_dialog", "browser_file_upload", "browser_wait_for", "browser_tab_select"}

FIND = types.Tool(
    name="browser_find_element",
    description=(
        "Find the page elements that match a test step, without reading the whole page. Give the step in plain "
        "words (e.g. 'Enter standard_user in the username field', 'Click Buy Now for the Halloween package'). "
        "Describing the element to act on works best ('the search box'). Returns a short list of candidate elements "
        "with their refs, best first; pass only the ref (e.g. e54) as `target` to "
        "browser_click, browser_type etc. Prefer this over browser_snapshot. If none of the candidates fits the step, or "
        "the check needs the whole page or a whole list (counts, all items, totals), call browser_snapshot."),
    input_schema={"type": "object", "properties": {
        "step": {"type": "string", "description": "The test step or action, in plain words"},
        "max": {"type": "integer", "description": "Optional cap on the number of candidates"}},
        "required": ["step"]},
)


class Finder:
    """Models are loaded on first use (a few seconds), then ranking takes milliseconds per step."""

    def __init__(self, lam):
        self.lam, self.rk = lam, None

    def find(self, snapshot, step, cap=None, details=None):
        from .common import doc
        els = merge_details(elements(snapshot), details)
        if not els:
            return [], 0, 0, True
        docs = [doc(e) for e in els]
        key = ("snap", hash(snapshot))  # live pages change: keep their embeddings in memory only
        if self.rk is None:
            from .lite import LiteRanker
            self.rk = LiteRanker()  # the ONNX models shipped in the package
        from .locate import rank_step
        s, lg = rank_step(self.rk, step, docs, key)  # data-entry steps: target element + step as written
        k = self.rk.cut(lg, self.lam) or 5
        s = demote_orphans(np.array(s, dtype=float), els)
        k = min(k, cap) if cap else k
        top = sorted(range(len(els)), key=lambda i: -s[i])[:k]
        from .locate import Locator
        top = Locator._ordinal_first(top, els, step, lambda e: (e.get("list") or (None,))[0])
        # generic ambiguity check: the best pick shares its label with another element and has no card to tell
        # them apart -> the shortlist alone may mislead, so recommend the full snapshot
        best = els[top[0]]
        dup = sum(1 for e in els if e["role"] == best["role"] and e["text"].lower() == best["text"].lower()) > 1
        full = sum(len(d) for d in docs) // 4
        return [els[i] for i in top], len(els), full, dup and not best.get("card")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lam", type=float, default=0.01, help="RL cut policy: higher = fewer candidates")
    ap.add_argument("--lean", action="store_true",
                    help="also start Playwright MCP with --snapshot-mode none (no full snapshot after every action)")
    ap.add_argument("--full-snapshots", action="store_true",
                    help="actions return Playwright MCP's full snapshot (default: only what changed on the page)")
    ap.add_argument("rest", nargs=argparse.REMAINDER, help="extra Playwright MCP args after --")
    a = ap.parse_args()
    cmd = shlex.split(os.environ.get("PLAYWRIGHT_MCP", "npx -y @playwright/mcp@0.0.83"))
    extra = [x for x in a.rest if x != "--"]
    params = StdioServerParameters(command=cmd[0], args=cmd[1:] + (["--snapshot-mode", "none"] if a.lean else []) + extra)
    finder = Finder(a.lam)

    async with stdio_client(params) as (r, w), ClientSession(r, w) as child:
        await child.initialize()

        async def list_tools(ctx, params):
            tools = (await child.list_tools()).tools
            return types.ListToolsResult(tools=[FIND] + list(tools))

        seen = {"last": None}  # the page as the LLM last saw it

        def text_of(res):
            return "".join(c.text for c in res.content if getattr(c, "type", "") == "text")

        def remember(text):
            _, yaml, url, title = split_snapshot(text)
            if yaml:
                seen["last"] = Snap(url, title, yaml)

        async def state():
            _, yaml, url, title = split_snapshot(text_of(await child.call_tool("browser_snapshot", {})))
            return Snap(url, title, yaml) if yaml else None

        async def action(name, args):
            """Forward an action; return what changed instead of the full snapshot. Late changes (slow server
            responses) are waited for up to WAIT_MAX s; positions that change by themselves are ignored."""
            pre, last = await state(), seen["last"]
            quiet = noise(last.snapshot, pre.snapshot) if pre and last and last.url == pre.url else frozenset()
            t0 = time.time()
            res = await child.call_tool(name, clean_args(name, args))
            text = text_of(res)
            rest, yaml, url, title = split_snapshot(text)
            after = Snap(url, title, yaml) if yaml else (None if res.is_error else await state())
            if after is None:
                return res
            while True:
                out = outcome(pre or last, after, quiet, time.time() - t0)
                if time.time() - t0 >= WAIT_MAX:
                    break
                await asyncio.sleep(POLL)
                nxt = (await state()) or after
                if "No change detected" not in out and nxt.snapshot == after.snapshot:
                    break  # changed and settled
                after = nxt
            seen["last"] = after
            out = f"{rest}\n\n### Result\n{out}".strip()
            other = [c for c in res.content if getattr(c, "type", "") != "text"]  # e.g. images stay as they are
            return types.CallToolResult(content=[types.TextContent(type="text", text=out)] + other, is_error=res.is_error)

        async def call_tool(ctx, params):
            args = params.arguments or {}
            if params.name in ACTIONS and not a.full_snapshots:
                return await action(params.name, args)
            if params.name != FIND.name:
                res = await child.call_tool(params.name, clean_args(params.name, args))
                if params.name == "browser_snapshot":
                    remember(text_of(res))
                return res
            snap = await child.call_tool("browser_snapshot", {})
            text = text_of(snap)
            remember(text)
            if snap.is_error or "```yaml" not in text:
                return types.CallToolResult(content=[types.TextContent(type="text", text=text or "No page open.")],
                                            is_error=True)
            details = None
            if needs_details(elements(text)):  # unnamed buttons / links: one DOM call describes them
                try:
                    raw = text_of(await child.call_tool("browser_evaluate", {"function": DETAILS_JS}))
                    res = raw.split("### Result", 1)[-1].split("###", 1)[0].strip()
                    details = json.loads(json.loads(res)) if res.startswith('"') else json.loads(res)
                except Exception:  # older Playwright MCP without browser_evaluate, or an unexpected answer
                    details = None
            cands, n, full, ambiguous = await asyncio.to_thread(finder.find, text, args.get("step", ""), args.get("max"),
                                                                details)
            page = text.split("### Snapshot", 1)[0].strip()
            short = sum(len(line(e)) for e in cands) // 4
            out = [page, "", f"### Candidates for: {args.get('step', '')}", *[line(e) for e in cands], "",
                   f"({len(cands)} of {n} elements, ~{short} tokens instead of ~{full} for the full snapshot.)"]
            if ambiguous:
                out.append("Note: the top candidate's label repeats on the page; call browser_snapshot before acting.")
            out.append("If no candidate fits the step, call browser_snapshot for the full page.")
            return types.CallToolResult(content=[types.TextContent(type="text", text="\n".join(out))])

        server = Server("lite-locator-mcp", version="0.1.7", on_list_tools=list_tools, on_call_tool=call_tool,
                        instructions="Use browser_find_element to locate elements for a step; browser_snapshot "
                                     "when no candidate fits or a check needs the whole page. Actions return what "
                                     "changed; if no change was detected, do not repeat a submit, payment or delete.")
        async with stdio_server() as (sr, sw):
            await server.run(sr, sw, server.create_initialization_options())


def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()
