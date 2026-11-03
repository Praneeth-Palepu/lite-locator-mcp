"""Write test cases from an exploration with lite_locator.evidence (fewer writer tokens, every negative path).

    pip install lite-locator-mcp langgraph langchain-mcp-adapters langchain-openai   # or langchain-ollama
    python write_test_cases.py > cases.md

1. A LangGraph agent explores the site for the acceptance criteria; the Recorder keeps the facts (no LLM calls).
2. probe() submits each form once per field left empty and records the exact messages (no LLM calls).
3. The writer reads the criteria and the compact record, not the whole exploration conversation.
"""
import asyncio
import sys
import os

from langchain_core.messages import HumanMessage
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.prebuilt import create_react_agent
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from lite_locator.evidence import Recorder, check, merge_cases, parse_cases, probe, render, writer_prompt
from lite_locator.langchain import LOCATOR_HINT, snapshot_hook, with_lite_locator

CRITERIA = [
    "A logged-in user with items in the cart can start checkout from the cart page.",
    "The checkout information step requires First Name, Last Name and Postal Code; each missing field shows an error "
    "message and the user cannot continue.",
    "Finish completes the order and shows a confirmation message.",
    "A locked-out user cannot log in and sees an error message.",
]
EXPLORE = """Explore the site to collect the facts a QA engineer needs to write test cases for these criteria:
{criteria}
Site: https://www.saucedemo.com  (users: standard_user and locked_out_user, password: secret_sauce)
Try the positive paths and the negative ones the criteria mention, and look at the exact messages shown.
When you have seen everything the criteria need, reply with DONE."""


def llm():
    if os.environ.get("AZURE_OPENAI_ENDPOINT"):  # AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT, OPENAI_API_VERSION
        from langchain_openai import AzureChatOpenAI
        return AzureChatOpenAI(azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"], temperature=0)
    from langchain_ollama import ChatOllama  # free, local
    return ChatOllama(model="qwen3:4b-instruct", temperature=0.2, num_ctx=16384, num_predict=3072, keep_alive=-1)


async def main():
    server = StdioServerParameters(command="npx", args=["-y", "@playwright/mcp@latest", "--headless", "--isolated"])
    async with stdio_client(server) as (r, w), ClientSession(r, w) as session:
        await session.initialize()
        rec = Recorder()
        tools = with_lite_locator(await load_mcp_tools(session), lean=True, recorder=rec)
        agent = create_react_agent(llm(), tools, prompt=LOCATOR_HINT, pre_model_hook=snapshot_hook)
        crit = "\n".join(f"{i}. {c}" for i, c in enumerate(CRITERIA, 1))
        await agent.ainvoke({"messages": [("user", EXPLORE.format(criteria=crit))]}, {"recursion_limit": 100})
        for line in await probe(tools, rec):          # the negative paths the agent did not try
            print("probe:", line, file=sys.stderr)
    observed = rec.observed_cases()
    reply = llm().invoke([HumanMessage(writer_prompt(CRITERIA, rec, observed))])
    cases = merge_cases(observed, parse_cases(reply.content))
    print(check(cases, CRITERIA, rec), file=sys.stderr)   # uncovered criteria, texts never seen
    print(render(cases, "markdown"))


if __name__ == "__main__":
    asyncio.run(main())
