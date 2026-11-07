"""Exploration evidence for test-case writers: see elem_rank.evidence."""
from elem_rank.evidence import Recorder, check, merge_cases, parse_cases, render, writer_prompt

__all__ = ["followup_hook", "Recorder", "writer_prompt", "parse_cases", "check", "render", "probe", "merge_cases"]


async def probe(tools, recorder, max_tries=12):
    """Recorder.probe with LangChain tools: pass the tools returned by with_lite_locator(..., recorder=recorder).
    Tries each untried negative path of the forms the exploration submitted, with no LLM calls."""
    by = {t.name.split("__")[-1]: t for t in tools}

    async def call(name, args):
        t = by.get(name)
        if t is None:
            if name == "browser_snapshot":   # navigate already returns the page state
                return ""
            raise ValueError(f"probe needs the {name} tool")
        schema = t.args_schema if isinstance(t.args_schema, dict) else t.args_schema.model_json_schema()
        props = schema.get("properties", {})
        if "ref" in args and "target" in props and "ref" not in props:
            args = {("target" if k == "ref" else k): v for k, v in args.items()}   # newer Playwright MCP
        return await t.ainvoke(args)

    return await recorder.probe(call, max_tries)


FOLLOW_UP = """{task}

The exploration of the site is finished. Instead of the exploration conversation, here is the library's record of it:
every page, every action and what it caused, and the exact messages the site showed{code_note}. Use these facts; do
not invent texts, values or locators that are not in it.

{record}"""


def followup_hook(recorder, code=True, max_steps=120, trim=True):
    """LangGraph pre_model_hook for an agent that explores a site and is then asked to do something with what it saw
    in the same conversation: write test cases, write test scripts, a bug report...

    While exploring it works like snapshot_hook. Once the agent has acted and a new instruction arrives (a new human
    message), every LLM call from then on gets the first task, the recorder's compact record and the messages since
    that instruction, instead of the whole exploration (which is what made writing cost as much as exploring).
    code=True adds the Playwright code each action ran (its locators), which script writers need.
    Use with with_lite_locator(..., recorder=recorder). The stored history is kept as it is; the library never writes
    the cases or scripts itself, it only decides what goes into the agent's calls.

    Prompt caching (OpenAI, Azure OpenAI, Gemini, Claude bill a repeated prefix at a fraction of the price): the
    writing-phase input is the same from call to call while the record does not change, so it caches. While exploring,
    trim=True drops superseded full snapshots, which changes earlier messages and so misses the cache from there on;
    with a caching provider, pass trim=False to keep the exploration history append-only."""
    from langchain_core.messages import HumanMessage

    from .langchain import trim_snapshots

    frozen = {}

    def hook(state):
        msgs = state["messages"]
        humans = [i for i, m in enumerate(msgs) if getattr(m, "type", "") == "human"]
        # the first instruction that came after the agent had acted: from there on, it is a follow-up
        follow = next((h for h in humans[1:] if any(getattr(m, "type", "") == "tool" for m in msgs[humans[0]:h])), None)
        if follow is None or not recorder.steps:
            return {"llm_input_messages": trim_snapshots(msgs) if trim else msgs}       # still exploring
        if frozen.get("at") != follow:   # the record as it was when the follow-up began: later actions are in the
            frozen["at"] = follow        # messages after it, and a fixed first message keeps the prompt cache hitting
            frozen["note"] = FOLLOW_UP.format(
                task=msgs[humans[0]].content, record=recorder.pack(max_steps=max_steps, code=code),
                code_note=" (with the Playwright code each action ran: its locators)" if code else "")
        note = frozen["note"]
        tail = msgs[follow:]
        return {"llm_input_messages": [HumanMessage(note)] + (trim_snapshots(tail) if trim else tail)}

    return hook
