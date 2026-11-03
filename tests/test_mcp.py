"""End to end with the real Playwright MCP server (needs Node/npx and the [mcp] extra): the proxy and the LangChain
wrapper against the local test pages."""
import asyncio
import os
import re
import shutil
import sys

import pytest

pytestmark = pytest.mark.skipif(shutil.which("npx") is None, reason="needs npx (Node) for Playwright MCP")


def _mcp_major():
    from importlib.metadata import PackageNotFoundError, version
    try:
        return int(version("mcp").split(".")[0])
    except PackageNotFoundError:
        return 0


# the proxy needs the [mcp] extra (mcp >= 2); LangChain environments keep mcp 1.x
needs_proxy = pytest.mark.skipif(_mcp_major() < 2, reason="the MCP proxy needs mcp >= 2 (the [mcp] extra)")
PW_MCP = os.environ.get("TEST_PW_MCP", "@playwright/mcp@0.0.83")  # mcp 1.3 clients need an older one (0.0.29)


def _text(res):
    return "".join(c.text for c in res.content if getattr(c, "type", "") == "text")


@needs_proxy
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


@needs_proxy
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


async def _negative_paths(find, click):
    """Shared by the proxy and the wrapper: a submit blocked by built-in validation and an error on the next page."""
    f = await find("click the Subscribe button")
    out = await click(re.search(r'button "Subscribe" \[ref=(e\d+)\]', f).group(1), "Subscribe")
    # Playwright MCP 0.0.29 has no browser_evaluate: the exact message can't be read, a hint says what may have happened
    assert ("built-in form validation" in out and '"Email"' in out) or "built-in validation may have blocked" in out, out
    f = await find("click the Sign in button")
    out = await click(re.search(r'button "Sign in" \[ref=(e\d+)\]', f).group(1), "Sign in")
    assert "Navigated" in out and "Invalid username or password" in out, out


@needs_proxy
def test_proxy_keeps_negative_path_messages(site):
    pytest.importorskip("mcp.server.lowlevel")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run():
        params = StdioServerParameters(command=sys.executable,
                                       args=["-m", "elem_rank.mcp_proxy", "--", "--headless", "--isolated"],
                                       env={"PLAYWRIGHT_MCP": f"npx -y {PW_MCP}", "PATH": os.environ["PATH"]})
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            await s.call_tool("browser_navigate", {"url": site + "/negative.html"})

            async def find(step):
                return _text(await s.call_tool("browser_find_element", {"step": step}))

            async def click(ref, element):
                return _text(await s.call_tool("browser_click", {"target": ref, "element": element}))
            await _negative_paths(find, click)

    asyncio.run(run())


def test_langchain_keeps_negative_path_messages(site, loc):
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
            await by["browser_navigate"].ainvoke({"url": site + "/negative.html"})
            key = "target" if "target" in str(by["browser_click"].args_schema) else "ref"

            async def find(step):
                return str(await by["browser_find_element"].ainvoke({"step": step}))

            async def click(ref, element):
                return str(await by["browser_click"].ainvoke({key: ref, "element": element}))
            await _negative_paths(find, click)

    asyncio.run(run())


def test_probe_finds_the_messages_of_untried_negative_paths(site, loc):
    """The explorer filled the form and went on; probe() leaves each field empty in turn, with no LLM, and records
    the exact message of each one. Cancel never counts as the form's submit button."""
    pytest.importorskip("langchain_mcp_adapters")
    from langchain_mcp_adapters.tools import load_mcp_tools
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from lite_locator.evidence import Recorder, probe
    from lite_locator.langchain import with_lite_locator

    async def run():
        params = StdioServerParameters(command="npx", args=["-y", PW_MCP, "--headless", "--isolated"])
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            rec = Recorder()
            tools = with_lite_locator(await load_mcp_tools(s), locator=loc, recorder=rec)
            by = {t.name: t for t in tools}
            key = "target" if "target" in str(by["browser_click"].args_schema) else "ref"
            await by["browser_navigate"].ainvoke({"url": site + "/shipping.html"})
            await by["browser_snapshot"].ainvoke({})
            refs = rec._refs()
            for name, value in [("First Name", "Ann"), ("Last Name", "Lee"), ("Postal Code", "12345")]:
                await by["browser_type"].ainvoke({key: refs[name], "element": name, "text": value})
            await by["browser_click"].ainvoke({key: refs['button "Cancel"'], "element": "Cancel"})
            await by["browser_navigate"].ainvoke({"url": site + "/shipping.html"})
            await by["browser_snapshot"].ainvoke({})
            refs = rec._refs()
            for name, value in [("First Name", "Ann"), ("Last Name", "Lee"), ("Postal Code", "12345")]:
                await by["browser_type"].ainvoke({key: refs[name], "element": name, "text": value})
            await by["browser_click"].ainvoke({key: refs['button "Continue"'], "element": "Continue"})
            assert [f["button"] for f in rec.forms()] == ['button "Continue"']
            assert len(rec.gaps()) == 1
            log = await probe(tools, rec)
            assert log == [f"/shipping.html {n} empty: Error: {n} is required"
                           for n in ("First Name", "Last Name", "Postal Code")], log
            assert rec.gaps() == []
            texts = [c["expected"] for c in rec.observed_cases()]
            for n in ("First Name", "Last Name", "Postal Code"):
                assert [f'"Error: {n} is required" is shown'] in texts
            case = next(c for c in rec.observed_cases() if "Last Name" in c["expected"][0])
            assert any("Leave Last Name empty" == st for st in case["steps"]), case

    asyncio.run(run())
