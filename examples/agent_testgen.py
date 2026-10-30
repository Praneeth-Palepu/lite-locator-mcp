"""A test agent like the ones teams already run (LangGraph + Playwright MCP), with lite-locator plugged in:
it executes a manual test case in the browser, then writes the Playwright test script from the code Playwright MCP
reports for each action, and prints how many tokens the LLM used.

    pip install lite-locator-mcp langgraph langchain-mcp-adapters langchain-openai    # or langchain-ollama
    # Playwright MCP started by the script (stdio):
    python agent_testgen.py --case case.txt --mcp-cmd "npx -y @playwright/mcp@0.0.29 --headless --isolated"
    # or a Playwright MCP server already running on a VM (SSE endpoint):
    python agent_testgen.py --case case.txt --mcp-url http://<vm-host>:8931/sse
    # the same run without lite-locator, to compare tokens:
    python agent_testgen.py --case case.txt --mcp-cmd "..." --no-lite

case.txt is a manual test case in plain words, e.g.:
    Title: Login and add the backpack to the cart
    1. Open https://www.saucedemo.com
    2. Log in as standard_user / secret_sauce
    3. Add the Sauce Labs Backpack to the cart
    4. Verify the cart shows 1 item

LLM: Azure OpenAI when AZURE_OPENAI_ENDPOINT is set (also AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT,
OPENAI_API_VERSION), else a local Ollama model. Works with mcp 1.x clients (tested with mcp 1.3 and
langchain-mcp-adapters 0.0.4) and newer ones.
"""
import argparse
import asyncio
import os
import re
import shlex
from contextlib import asynccontextmanager

from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.prebuilt import create_react_agent
from mcp import ClientSession

from lite_locator.langchain import LOCATOR_HINT, snapshot_hook, with_lite_locator

NEEDED = {"browser_navigate", "browser_snapshot", "browser_click", "browser_type", "browser_select_option",
          "browser_press_key", "browser_hover", "browser_wait_for", "browser_handle_dialog", "browser_navigate_back"}
SYSTEM = ("You are a QA automation agent. Execute the given manual test case step by step in the browser with the "
          "browser tools. After the last step, reply with PASS or FAIL and one line explaining why.")


def make_llm():
    if os.environ.get("AZURE_OPENAI_ENDPOINT"):
        from langchain_openai import AzureChatOpenAI
        return AzureChatOpenAI(azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"], temperature=0)
    from langchain_ollama import ChatOllama
    return ChatOllama(model=os.environ.get("OLLAMA_MODEL", "qwen3:4b-instruct"), temperature=0, num_ctx=8192,
                      keep_alive=-1)


@asynccontextmanager
async def playwright_mcp(cmd=None, url=None):
    """A client session to Playwright MCP: started here (stdio) or an existing server (SSE URL)."""
    if url:
        from mcp.client.sse import sse_client
        transport = sse_client(url)
    else:
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client
        parts = shlex.split(cmd)
        transport = stdio_client(StdioServerParameters(command=parts[0], args=parts[1:]))
    async with transport as (r, w), ClientSession(r, w) as session:
        await session.initialize()
        yield session


def script_from(messages, title):
    """The Playwright test: the code lines Playwright MCP reports under 'Ran Playwright code' for every action."""
    lines = []
    for m in messages:
        if getattr(m, "type", "") != "tool" or m.name in ("browser_snapshot", "browser_find_element"):
            continue
        for block in re.findall(r"```js\n(.*?)```", str(m.content), re.S):
            lines += [x for x in block.strip().splitlines() if x.strip() and not x.strip().startswith("//")]
    body = "\n".join("  " + x for x in lines)
    return (f"import {{ test, expect }} from '@playwright/test';\n\ntest({title!r}, async ({{ page }}) => {{\n"
            f"{body}\n}});\n")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True, help="manual test case, plain text")
    ap.add_argument("--mcp-cmd", default="npx -y @playwright/mcp@0.0.29 --headless --isolated")
    ap.add_argument("--mcp-url", help="SSE URL of a running Playwright MCP server (instead of --mcp-cmd)")
    ap.add_argument("--no-lite", action="store_true", help="plain Playwright MCP, for comparison")
    ap.add_argument("--all-tools", action="store_true", help="give the LLM every Playwright MCP tool (default: only "
                    "the ones test execution needs; each tool definition is sent with every LLM call)")
    ap.add_argument("--out", default="generated.spec.js")
    a = ap.parse_args()
    case = open(a.case).read().strip()
    title = next((x.split(":", 1)[1].strip() for x in case.splitlines() if x.lower().startswith("title:")), "test")

    async with playwright_mcp(a.mcp_cmd, a.mcp_url) as session:
        tools = await load_mcp_tools(session)
        if not a.all_tools:  # tool definitions are re-sent on every LLM call: keep only what test execution needs
            tools = [t for t in tools if t.name in NEEDED]
        if a.no_lite:
            agent = create_react_agent(make_llm(), tools, prompt=SYSTEM)
        else:
            tools = with_lite_locator(tools)                            # find tool + actions return what changed
            agent = create_react_agent(make_llm(), tools, prompt=LOCATOR_HINT + SYSTEM,
                                       pre_model_hook=snapshot_hook)    # old full snapshots are not re-read
        result = await agent.ainvoke({"messages": [("user", case)]}, {"recursion_limit": 60})

    msgs = result["messages"]
    used = [m.usage_metadata for m in msgs if getattr(m, "usage_metadata", None)]
    tools_called = [m.name for m in msgs if getattr(m, "type", "") == "tool"]
    open(a.out, "w").write(script_from(msgs, title))
    print("verdict:", msgs[-1].content.strip()[:300])
    print("tools called:", ", ".join(tools_called))
    print(f"LLM calls: {len(used)}  input tokens: {sum(u['input_tokens'] for u in used):,}  "
          f"output tokens: {sum(u['output_tokens'] for u in used):,}")
    print(f"test script written to {a.out}")


if __name__ == "__main__":
    asyncio.run(main())
