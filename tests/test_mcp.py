"""End to end with the real Playwright MCP server (needs Node/npx and the [mcp] extra): the proxy and the LangChain
wrapper against the local test pages."""
import asyncio
import os
import re
import shutil
import sys

import pytest

pytestmark = pytest.mark.skipif(shutil.which("npx") is None, reason="needs npx (Node) for Playwright MCP")
PW_MCP = os.environ.get("TEST_PW_MCP", "@playwright/mcp@0.0.83")  # mcp 1.3 clients need an older one (0.0.29)


def _text(res):
    return "".join(c.text for c in res.content if getattr(c, "type", "") == "text")


def test_proxy_find_and_diff(site):
    pytest.importorskip("mcp.server.lowlevel")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run():
        params = StdioServerParameters(command=sys.executable,
                                       args=["-m", "elem_rank.mcp_proxy", "--", "--headless", "--isolated"],
                                       env={"PLAYWRIGHT_MCP": f"npx -y {PW_MCP}", "PATH": os.environ["PATH"]})
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            names = [t.name for t in (await s.list_tools()).tools]
            assert names[0] == "browser_find_element" and "browser_click" in names
            await s.call_tool("browser_navigate", {"url": site + "/login.html"})
            f = _text(await s.call_tool("browser_find_element", {"step": "click the login button"}))
            ref = re.search(r'button "Login" \[ref=(e\d+)\]', f).group(1)
            out = _text(await s.call_tool("browser_click", {"target": ref}))
            assert "### Result" in out and "+ alert" in out and "do not match" in out
            assert "```yaml" not in out

    asyncio.run(run())


def test_langchain_with_real_mcp(site, loc):
    pytest.importorskip("langchain_mcp_adapters")
    from langchain_mcp_adapters.tools import load_mcp_tools
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from lite_locator.langchain import with_lite_locator

    async def run():
        params = StdioServerParameters(command="npx", args=["-y", PW_MCP, "--headless", "--isolated"])
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            by = {t.name: t for t in with_lite_locator(await load_mcp_tools(s), locator=loc)}
            await by["browser_navigate"].ainvoke({"url": site + "/grid.html"})
            f = str(await by["browser_find_element"].ainvoke({"step": "add the Sauce Labs Onesie to the cart"}))
            ref = re.search(r"\[ref=(e\d+)\]", f).group(1)
            assert "Onesie" in f.splitlines()[[i for i, x in enumerate(f.splitlines()) if "[ref=" in x][0]]
            key = "target" if "target" in str(by["browser_click"].args_schema) else "ref"  # newer / older Playwright MCP
            out = str(await by["browser_click"].ainvoke({key: ref, "element": "add to cart"}))
            assert "Cart, 1 items" in out and "```yaml" not in out

    asyncio.run(run())


def test_proxy_describes_icon_only_buttons(site):
    pytest.importorskip("mcp.server.lowlevel")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run():
        params = StdioServerParameters(command=sys.executable,
                                       args=["-m", "elem_rank.mcp_proxy", "--", "--headless", "--isolated"],
                                       env={"PLAYWRIGHT_MCP": f"npx -y {PW_MCP}", "PATH": os.environ["PATH"]})
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            await s.call_tool("browser_navigate", {"url": site + "/icons.html"})
            f = _text(await s.call_tool("browser_find_element", {"step": "Add the product to the cart"}))
            first = next(x for x in f.splitlines() if "[ref=" in x)
            assert "add to cart" in first.lower(), f

    asyncio.run(run())
