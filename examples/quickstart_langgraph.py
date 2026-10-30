"""Quickstart 2: lite-locator in a LangGraph agent that drives Playwright MCP (your existing setup).

    pip install lite-locator-mcp langgraph langchain-mcp-adapters langchain-openai   # or langchain-ollama
    python quickstart_langgraph.py

Three lines change in an existing agent (marked ★):
  ★1 with_lite_locator(tools)       adds browser_find_element; actions return what changed, not full snapshots
  ★2 LOCATOR_HINT in the prompt      tells the LLM to find elements first, full snapshot only as a fallback
  ★3 pre_model_hook=snapshot_hook    the LLM no longer re-reads old full snapshots on every call
Works with any mcp version (1.x included): it only wraps the LangChain tools you already load.
"""
import asyncio
import os

from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.prebuilt import create_react_agent
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from lite_locator.langchain import LOCATOR_HINT, snapshot_hook, with_lite_locator

TASK = ("Go to https://www.saucedemo.com, log in as standard_user with password secret_sauce, add the Sauce Labs "
        "Backpack to the cart, and verify the cart shows 1 item.")


def llm():
    if os.environ.get("AZURE_OPENAI_ENDPOINT"):  # AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT, OPENAI_API_VERSION
        from langchain_openai import AzureChatOpenAI
        return AzureChatOpenAI(azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"], temperature=0)
    from langchain_ollama import ChatOllama  # free, local
    return ChatOllama(model="qwen3:4b-instruct", temperature=0, num_ctx=8192, keep_alive=-1)


async def main():
    # your existing Playwright MCP server: same command, version and flags as in your config (with an mcp 1.x
    # Python client keep the older Playwright MCP you already use, e.g. @playwright/mcp@0.0.29)
    server = StdioServerParameters(command="npx", args=["-y", "@playwright/mcp@latest", "--headless", "--isolated"])
    async with stdio_client(server) as (r, w), ClientSession(r, w) as session:
        await session.initialize()
        tools = await load_mcp_tools(session)

        tools = with_lite_locator(tools)                                      # ★1
        agent = create_react_agent(llm(), tools,
                                   prompt=LOCATOR_HINT + "You are a QA test agent.",  # ★2
                                   pre_model_hook=snapshot_hook)              # ★3

        result = await agent.ainvoke({"messages": [("user", TASK)]}, {"recursion_limit": 40})
        for m in result["messages"]:
            if m.type == "tool":
                print(f"\n[{m.name}]\n{str(m.content)[-400:]}")
        print("\nFINAL:", result["messages"][-1].content)


asyncio.run(main())
