"""LangChain wrapper with fake Playwright MCP tools (no browser): tool set, diffs, history trimming."""
import asyncio

import pytest

pytest.importorskip("langchain_core")
from langchain_core.messages import HumanMessage, ToolMessage  # noqa: E402
from langchain_core.tools import StructuredTool  # noqa: E402

from lite_locator.langchain import snapshot_hook, trim_snapshots, with_lite_locator  # noqa: E402

PAGE1 = "- Page URL: http://x/\n- Page Title: Login\n- Page Snapshot\n```yaml\n- textbox \"Username\" [ref=e1]\n- button \"Login\" [ref=e2]\n```"
PAGE2 = "- Page URL: http://x/\n- Page Title: Login\n- Page Snapshot\n```yaml\n- textbox \"Username\" [ref=e1]\n- button \"Login\" [ref=e2]\n- alert [ref=e3]:\n  - text: Wrong password\n```"


def fake_tools(state):
    def snapshot() -> str:
        return state["page"]

    def browser_click(ref: str) -> str:
        state["page"] = PAGE2
        return "- Ran Playwright code:\n```js\nawait page.click()\n```\n" + PAGE2

    return [StructuredTool.from_function(snapshot, name="browser_snapshot", description="snapshot"),
            StructuredTool.from_function(browser_click, name="browser_click", description="click"),
            StructuredTool.from_function(lambda: "ok", name="browser_close", description="close")]


def test_find_tool_added_and_others_kept(loc):
    tools = with_lite_locator(fake_tools({"page": PAGE1}), locator=loc)
    assert [t.name for t in tools] == ["browser_find_element", "browser_snapshot", "browser_click", "browser_close"]


def test_action_returns_change_not_snapshot(loc):
    by = {t.name: t for t in with_lite_locator(fake_tools({"page": PAGE1}), locator=loc)}
    assert "[ref=e1]" in asyncio.run(by["browser_find_element"].ainvoke({"step": "username"}))
    out = asyncio.run(by["browser_click"].ainvoke({"ref": "e2"}))
    assert "```yaml" not in out and "+ alert" in out and "Wrong password" in out
    assert "await page.click()" in out  # Playwright's own code lines are kept


def test_diffs_off_keeps_tools_unchanged(loc):
    by = {t.name: t for t in with_lite_locator(fake_tools({"page": PAGE1}), locator=loc, diffs=False)}
    assert "```yaml" in by["browser_click"].invoke({"ref": "e2"})


def test_full_snapshot_still_available(loc):
    by = {t.name: t for t in with_lite_locator(fake_tools({"page": PAGE1}), locator=loc)}
    assert "```yaml" in by["browser_snapshot"].invoke({})


def test_trim_keeps_only_latest_snapshot():
    msgs = [HumanMessage("go"), ToolMessage(PAGE1, tool_call_id="1"), ToolMessage(PAGE2, tool_call_id="2")]
    out = trim_snapshots(msgs)
    assert "removed" in out[1].content and "```yaml" in out[2].content and msgs[1].content == PAGE1
    assert snapshot_hook({"messages": msgs})["llm_input_messages"][1].content == out[1].content
