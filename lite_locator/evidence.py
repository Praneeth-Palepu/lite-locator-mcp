"""Exploration evidence for test-case writers: see elem_rank.evidence."""
from elem_rank.evidence import Recorder, check, merge_cases, parse_cases, render, writer_prompt

__all__ = ["Recorder", "writer_prompt", "parse_cases", "check", "render", "probe", "merge_cases"]


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
