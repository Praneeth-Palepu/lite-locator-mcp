# Integrating lite-locator into an existing test agent

For teams that already run Python test agents on LangGraph / LangChain with Playwright MCP: what to install, which
lines change, how the agent behaves afterwards, how to generate scripts, how to measure the savings, and how to roll
it out safely. Runnable example: [`examples/agent_testgen.py`](../examples/agent_testgen.py).

## 1. Where it sits

```
manual test case ──► LangGraph agent (LLM) ──tool calls──► Playwright MCP ──► browser
                          ▲                      │
                          │      lite-locator wraps the tools (in the agent's own Python process):
                          │      • adds browser_find_element(step)  → a shortlist, not the whole page
                          └───── • browser_click / type / navigate … → what changed, not a new snapshot
```

Nothing changes on the Playwright MCP server (local or on a VM), in its version or its flags. lite-locator runs inside
the agent's Python process and wraps the LangChain tools the agent already loads. The models run on the CPU of that
machine (a few hundred MB of RAM, ~0.2 s per step); nothing is downloaded at runtime.

## 2. Install (in the agent's environment)

```bash
pip install --upgrade lite-locator-mcp
python -c "import lite_locator; print(lite_locator.__version__)"
```

- **Do not install the `[mcp]` extra here.** It pulls mcp 2.x, which breaks `langchain-mcp-adapters` (they need mcp 1.x).
  The library itself has no MCP dependency and leaves your installed `mcp` (1.3 or newer 1.x) untouched.
- Windows: `onnxruntime` needs the Microsoft Visual C++ 2015–2022 runtime (check: `python -c "import onnxruntime"`).
- No Hugging Face access is needed (the models are inside the package).

## 3. The code change (three lines)

Where your agent loads the Playwright MCP tools and builds the agent:

```python
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.prebuilt import create_react_agent
from lite_locator.langchain import with_lite_locator, LOCATOR_HINT, snapshot_hook

tools = await load_mcp_tools(session)                 # your existing session to Playwright MCP (stdio or SSE)
tools = with_lite_locator(tools)                      # ★1
agent = create_react_agent(llm, tools,
                           prompt=LOCATOR_HINT + YOUR_SYSTEM_PROMPT,   # ★2
                           pre_model_hook=snapshot_hook)               # ★3
```

| Line | What it does | Turn it off |
|---|---|---|
| ★1 `with_lite_locator(tools)` | Adds `browser_find_element`; wraps `browser_click`, `browser_type`, `browser_navigate`, `browser_select_option`, `browser_hover`, `browser_press_key`, … so they return what changed | `with_lite_locator(tools, diffs=False)` keeps the action tools unchanged (find tool only) |
| ★2 `LOCATOR_HINT` | Tells the LLM: find first; full snapshot when nothing fits or for whole-page checks; don't repeat a submit/payment/delete after "no change" | leave it out |
| ★3 `snapshot_hook` | The LLM no longer re-reads old full snapshots on every call (the stored history keeps them) | leave it out |

To give the LLM only the tools test execution needs (fewer tokens on every call), pass the full list and name the
ones to expose: `with_lite_locator(tools, keep={"browser_navigate", "browser_click", "browser_type", ...})`. Don't
filter the list before wrapping: the library uses `browser_snapshot` and `browser_evaluate` itself.

If your agent is a hand-written LangGraph graph instead of `create_react_agent`, use `trim_snapshots(messages)` on the
messages you pass to the LLM (same effect as ★3).

### Connecting to Playwright MCP on a VM

The session is yours, so any transport works. For a Playwright MCP server already running on a VM with an SSE endpoint:

```python
from mcp import ClientSession
from mcp.client.sse import sse_client

async with sse_client("http://<vm-host>:8931/sse") as (r, w), ClientSession(r, w) as session:
    await session.initialize()
    tools = with_lite_locator(await load_mcp_tools(session))
```

(`langchain-mcp-adapters`' `MultiServerMCPClient` works too; wrap the tools it returns the same way.)

## 4. What the agent does per step afterwards

Test step: *"Add the Sauce Labs Backpack to the cart"*

1. The LLM calls `browser_find_element(step="Add the Sauce Labs Backpack to the cart")` and gets:
   ```
   - button "Add to cart" [ref=e54]  (card: Sauce Labs Backpack; in: main)
   - button "Add to cart" [ref=e102] (card: Sauce Labs Onesie; in: main)
   (2 of 47 elements, ~44 tokens instead of ~1039 for the full page.)
   ```
2. It calls `browser_click(ref="e54")` and gets Playwright's code line plus the result:
   ```
   await page.locator('[data-test="add-to-cart-sauce-labs-backpack"]').click();
   ### Result
   Same page. Changes:
   + button "Cart, 1 items"
   - button "Cart, empty"
   + button "Remove"  (card: Sauce Labs Backpack)
   ```
3. Next step. If no candidate fits, or the step checks a whole list ("all 6 products have prices"), the LLM calls
   `browser_snapshot` (the full tree): the fallback is always there.

Possible results after an action: `Navigated to a new page …` (with a short page summary), `Same page. Changes: …`,
`No change detected within 3 s …` (after waiting for slow responses; the LLM is told not to repeat irreversible
actions), plus `A new tab opened: …` / `A browser dialog appeared: …` when that happens.

## 5. Generating the test script

Playwright MCP reports the exact code for every action ("Ran Playwright code"), and the wrapped tools keep it. Collect
those lines from the tool messages after the run (as `script_from()` in the example does):

```js
import { test, expect } from '@playwright/test';
test('Login and add the backpack to the cart', async ({ page }) => {
  await page.goto('https://www.saucedemo.com');
  await page.locator('[data-test="username"]').fill('standard_user');
  await page.locator('[data-test="password"]').fill('secret_sauce');
  await page.locator('[data-test="login-button"]').click();
  await page.locator('[data-test="add-to-cart-sauce-labs-backpack"]').click();
});
```

That is the real output of `examples/agent_testgen.py` on that test case. Assertions: verification steps are judged
by the LLM from the results; to get `expect(...)` lines in the script, give the agent Playwright MCP's verify tools
(newer versions with `--caps verify`) or add them from the step text in your script generator.

For Python code generation without MCP (agent drives a Playwright page directly), each `Locator.find()` hit has a
ready Python locator: `hits[0]["py"]` → `page.locator("[data-test=\"username\"]")`.

## 6. Measuring the savings on your own flows

Run the same test case twice, with and without the three lines, and compare the LLM's token usage (every LangChain
chat model reports `usage_metadata` on its messages):

```bash
python agent_testgen.py --case case.txt --mcp-url http://<vm>:8931/sse            # with lite-locator
python agent_testgen.py --case case.txt --mcp-url http://<vm>:8931/sse --no-lite  # plain Playwright MCP
```

Measured here (SauceDemo, local Qwen3 4B, Playwright MCP 0.0.83):

| Setup | Result | LLM input tokens |
|---|---|---|
| plain Playwright MCP, all tools | FAIL (could not locate the login form) | 65,978 |
| lite-locator, all tools | PASS, correct script | 64,660 |
| lite-locator, only the 10 tools test execution needs | PASS, correct script | 31,746 |

Two lessons: a small model completes the flow only with the shortlist; and on small pages the biggest cost is the
**tool definitions themselves**, sent with every LLM call: give the agent only the tools it needs (the example's
default). On heavy pages (10,000+ tokens per snapshot) the shortlist and the change reports dominate the savings
(13-step SauceDemo replay: 220,771 → 29,119 tokens re-read across calls). Measure on your own suites; numbers vary
with the model and the pages.

## 7. Rolling out safely

1. **Shadow run:** run your existing suite as usual and, in parallel, the same cases with the three lines. Compare
   pass/fail per case and tokens. No change to production runs.
2. **Pilot:** switch one suite; keep `diffs=False` first if you want only the shortlist, then turn diffs on.
3. **Watch** for steps where the LLM falls back to `browser_snapshot` often: those pages are hard for the shortlist
   (non-English text, unusual widgets). They stay correct, just cost more.
4. Everything is reversible by removing the three lines.

## 8. Limits

- English pages (the models are English); other languages fall back to the full snapshot more often.
- Whole-page / whole-list verifications need the full snapshot (the hint says so).
- Visual-only changes (colours, canvas) are not in accessibility snapshots at all.
- With `mcp` 1.x Python clients, keep the Playwright MCP version you already use; very new Playwright MCP servers may
  not answer older clients (seen with mcp 1.3 and Playwright MCP 0.0.83; 0.0.29 works).
- `langchain-mcp-adapters` 0.0.4 produces tool schemas that some LLM servers reject (seen with Ollama, with or without
  lite-locator); newer adapter versions are fine.

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `browser_find_element` missing | `with_lite_locator` was not applied to the tool list the agent gets |
| `ImportError ... mcp.server.fastmcp` | mcp 2.x installed next to langchain-mcp-adapters: `pip install "mcp<2"` (and don't use `[mcp]`) |
| `DLL load failed` on Windows | install the Visual C++ 2015–2022 runtime |
| Tool calls hang with an mcp 1.x client | use an older Playwright MCP server (e.g. `@playwright/mcp@0.0.29`) |
| LLM keeps calling `browser_snapshot` | check the step wording; non-English pages; or the page has no accessible names |
