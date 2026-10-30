# lite-locator-mcp

A semantic element finder for Playwright test agents: give it a page and a test step, get back a short list of
candidate elements (with Playwright locators or MCP refs) to put in the LLM prompt instead of the whole accessibility
tree. Use it as a Python library inside your own agents (LangChain/LangGraph tools included), or as an MCP proxy.

## As a library (your own agents)

```python
from lite_locator import Locator
loc = Locator()                                    # loads the bundled models once
hits = loc.find(page, "Enter standard_user in the username field")     # Playwright sync Page
hits = await loc.afind(page, step)                 # Playwright async Page
hits = loc.find(snapshot_text, step)               # Playwright MCP snapshot text you already have
hits[0]["locator"]      # page.locator('[data-test="username"]'), checked on the live page (or hits[0]["ref"])
hits.prompt             # short text for the LLM prompt
hits.tokens, hits.full_tokens, hits.fallback_full_tree   # True: send the LLM the full tree for this step
```

Actions return what changed, not a new snapshot (snapshots no longer pile up in the history):

```python
from lite_locator import act
act(page, hits[0], "fill", "standard_user")   # 'Same page. Changes: ~ textbox "Username" -> ...: standard_user'
act(page, loc.find(page, "login button")[0], "click")   # 'Navigated to a new page. Page: ... Has: 20 buttons, ...'
# or "No visible change on the page (the action may not have worked)."
```

LangChain / LangGraph, with Playwright MCP through `langchain-mcp-adapters` (any `mcp` version):

```python
from lite_locator.langchain import with_lite_locator, page_tool, snapshot_hook, LOCATOR_HINT
tools = with_lite_locator(await load_mcp_tools(session))   # adds browser_find_element, keeps every tool
tools = [page_tool(page)]                                   # or: a tool over a Playwright Python page
agent = create_react_agent(llm, tools, pre_model_hook=snapshot_hook)  # drops superseded full snapshots
```

With `with_lite_locator`, browser_click / browser_type / browser_navigate … return the change (navigation summary,
added/removed/changed elements, or no change) instead of a full snapshot; `diffs=False` keeps them unchanged.
Measured on a 7-step SauceDemo flow: 31,145 → 8,272 tokens re-read across the agent's LLM calls (−73%).

Controls built only from JavaScript (a plain `<div>English</div>` with a click handler, no button role) are still
found: on a Playwright page the library checks click listeners through Chromium's DevTools protocol and marks such
text as clickable; on an MCP snapshot, short text without its own ref is offered through its container's ref.

The base install has no MCP dependency, so it sits next to an existing `mcp` 1.x without changing it.

## As an MCP proxy

The proxy runs Playwright MCP as a child process and exposes
all of its tools unchanged, plus `browser_find_element(step)`. That tool takes the accessibility snapshot inside the
proxy, ranks the elements against the test step with small local models, and returns only a shortlist with refs. The
LLM acts on a ref (`browser_click target=e990`) and calls `browser_snapshot` only when nothing in the shortlist fits.

Measured on the hard held-out test set (2,875 steps, 38 sites never seen in training), the correct element was in a
shortlist of about 150–500 tokens 82–90% of the time. The full page averaged 12,229 tokens. Playwright MCP runs exactly as configured;
`--lean` adds `--snapshot-mode none`, so clicks and typing no longer return a full snapshot each time.

## Install

Needs Python 3.10+ and Node 18+ (for Playwright MCP itself).

```
pip install lite-locator-mcp          # library
pip install "lite-locator-mcp[mcp]"   # MCP proxy (needs mcp>=2.3: use its own venv if you have an older mcp)
npx playwright install chromium                           # once, if no browser is installed
```

## Use in an MCP client

`.mcp.json` (Claude Code, Cursor, etc.):

```json
{"mcpServers": {"playwright": {"command": "lite-locator-mcp", "args": ["--", "--headless", "--isolated"]}}}
```

Arguments after `--` are passed to Playwright MCP. `--lam` sets the cut policy (0.002, 0.005, 0.01 or 0.02; a
higher value shows fewer candidates; the default is 0.01).

Environment variables:
- `PLAYWRIGHT_MCP` overrides the child command (default `npx -y @playwright/mcp@0.0.83`).
- `ELEM_RANK_LITE` points to a different model bundle.

## Build the wheel (from the repo)

```
.venv/bin/python -m elem_rank.export_onnx --out elem_rank/models-lite   # ONNX int8 models, 57 MB
.venv/bin/python -m build      # sdist + wheel in dist/
```

## Credits and licences

- Code: MIT (see LICENSE).
- Retriever fine-tuned from [BAAI/bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5) (MIT).
- Reranker fine-tuned from [cross-encoder/ms-marco-MiniLM-L6-v2](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2) (Apache-2.0).
- Training data includes [Mind2Web](https://osu-nlp-group.github.io/Mind2Web/) (Deng et al., 2023, CC BY 4.0) and
  pages from public websites.
