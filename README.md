# lite-locator-mcp

Fewer LLM tokens and sturdier generated tests for Playwright test automation. Small local models (CPU, inside the
package) find the page elements for each test step, so the LLM no longer reads whole pages or re-reads a growing
conversation.

## Wins (0.1.6, measured)

One user story on [practicesoftwaretesting.com](https://practicesoftwaretesting.com) (a site built for test
practice), 34 manual test cases (search, filters, sorting, product page, quantity, cart), local Qwen3 4B model:

| | Agent: LangGraph + Playwright MCP + lite-locator 0.1.5 | **TestRunner (0.1.6)** |
|---|---|---|
| Test cases passed | 30 / 34 after healing | **34 / 34** |
| Generated scripts that pass `npx playwright test` | broke after the site's next data reset | **34 / 34**, also with 4 parallel workers |
| LLM calls (run + healing) | 658 | **68** |
| LLM tokens (input + output) | 2,528,293 + 24,081 | **22,413 + 5,083** |
| Cost at GPT-5.4 prices ($2.50 / $15 per 1M tokens) | ≈ $6.68 per user story | **≈ $0.13 per user story** (≈ $0.004 per test case) |

TestRunner column: one full run; a second full run gave 33 / 34 (the model left an expected value out of one plan;
such values are now taken from the test case text) with 84 calls and 27,055 + 5,724 tokens (≈ $0.15). A plain
Playwright MCP agent without lite-locator, same model, failed its first 3 cases at 15,000 tokens per case and one
reply ran away for 40 minutes: a small model cannot work from 3,000–4,000-token page snapshots.

Where the wins come from:

1. **Plan once, the library executes** (`TestRunner`): one LLM call turns a manual test case into steps; the library
   finds and acts on each element itself and asks the LLM only when it is unsure (a ~300-token question with a short
   list). An agent instead re-sends the whole conversation for every step.
2. **Stable locators in generated code**: no database IDs (`[data-test="product-01M66W2R…"]`) and no positional CSS
   paths. Elements are located by label, card heading, table row or text, and only when that matches exactly one
   element. Applies to `find()`, `TestRunner` and the LangGraph wrapper.
3. **Scripts wait for what changed**: after a click the script asserts the new page title, or the badge that went from
   1 to 2, so it never races ahead of the app.
4. **Icon-only buttons are found** (✕ delete, 🗑, +): described by their icon and table row
   (`icon=xmark (remove / delete / close) | row: Claw Hammer`).
5. **Shortlist instead of the whole page** and **what changed instead of a new snapshot** for LLM agents (since 0.1.3):
   a few candidates at 40–150 tokens instead of 2,000–12,000 for the page.

Runs on CPU, no GPU and no PyTorch; the int8 ONNX models are inside the package and nothing is downloaded at runtime.

## Install

| You use | Install | Notes |
|---|---|---|
| `TestRunner` or your own Python code with a Playwright page | `pip install lite-locator-mcp playwright` | then `python -m playwright install chromium` |
| LangChain / LangGraph with `langchain-mcp-adapters` | `pip install lite-locator-mcp` | Works with any `mcp` 1.x (tested with 1.3). **Don't add `[mcp]`**: `langchain-mcp-adapters` needs mcp 1.x. |
| An MCP client (Claude Code, Cursor, Copilot) | `pip install "lite-locator-mcp[mcp]"` | The proxy needs mcp ≥ 2.3; put it in its own venv if something else needs mcp 1.x. |

Python 3.10+. Node 18+ for Playwright MCP and for running the generated `.spec.js` files. On Windows, `onnxruntime`
needs the Microsoft Visual C++ 2015–2022 runtime (usually already installed).

## 1. Run manual test cases and get Playwright scripts (`TestRunner`)

```python
from langchain_ollama import ChatOllama          # or AzureChatOpenAI, or any function: prompt -> text
from playwright.sync_api import sync_playwright
from lite_locator.runner import TestRunner

case = """Title: Search shows matching products
1. Open https://practicesoftwaretesting.com
2. Type pliers in the search box
3. Click the search button
4. Verify the text "Searched for: pliers" is shown
5. Verify the product Thor Hammer is not listed"""

llm = ChatOllama(model="qwen3:4b-instruct", temperature=0)
runner = TestRunner(llm, planner=ChatOllama(model="qwen3:4b-instruct", temperature=0, format="json"))
with sync_playwright() as p:
    result = runner.run(p.chromium.launch().new_page(), case)

print(result.passed)          # True
for s in result.log:          # one entry per step: what was done, and who chose the element
    print(s["ok"], s["chosen_by"], s["step"])
print(result.usage)           # LLM calls and tokens: {'plan': {'calls': 1, ...}, 'choose': {...}}
open("search.spec.js", "w").write(result.spec("Search shows matching products"))
```

A whole suite from the command line ([`examples/run_test_cases.py`](examples/run_test_cases.py)): runs every case,
re-runs failures with the LLM choosing every element (the healer), writes one `.spec.js` per case, runs them with
Playwright Test and prints the token usage.

```bash
python examples/run_test_cases.py --cases cases.txt --out specs/ --check-scripts
# TC01 PASS  Search by name shows matching products
# ...
# LLM usage
#   run     53 calls    18,410 input    4,732 output tokens
#   heal    15 calls     4,003 input      351 output tokens
#   34/34 test cases passed; scripts in specs/
```

A generated script (cart, two products, delete one; note the waits and the row-scoped icon button):

```js
await page.goto("https://practicesoftwaretesting.com");
await page.waitForLoadState('networkidle');
await page.getByRole("heading", { name: "Combination Pliers", exact: true }).click();
await expect(page).toHaveTitle("Combination Pliers - Practice Software Testing - Toolshop - v5.0");
await page.locator('[data-test="add-to-cart"]').click();
await expect(page.locator("[data-test=\"nav-cart\"]")).toHaveText("1");
// ... second product, open the cart ...
await page.getByRole('row').filter({ hasText: "Claw Hammer" }).locator("a.btn.btn-danger").click();
await expect(page.getByText("Claw Hammer").filter({ visible: true })).toHaveCount(0);
```

Real test cases are messier than the one above. Tested with 16 cases on another site written as "Steps | Expected
Result" rows with preconditions, one-line prose ("add any phone to the cart and check it shows up"), Gherkin and jargon
(PLP → PDP, ATC): 0.1.7 passes 13/16 (13 of the 15 that can pass) with a local 4B model and 12/16 with a stronger model for the planning call
(one case has no test data on purpose). What it handles: preconditions become setup steps, one sentence becomes
several single actions, dialogs and menus the case doesn't mention are opened first, browser popups are checked, and
"any item" means the first one listed. What it can't do: invent data the case doesn't give ("log in with valid
credentials"), so put the data in the test case.

Quality guards: the library acts alone only when its top match is clearly ahead, fits the action (type → a text box)
and is unique, or when the element's own title is exactly what the step names ("Claw Hammer", not "Claw Hammer with
Shock Reduction Grip"); a click that changes nothing goes back to the LLM; verify steps are checked in the browser by
code, and a value mismatch is shown to the LLM before the step fails; values come from the test case text, not from
the model's copy of it. Step types: open, click, type, select, check, uncheck, press, back, verify text / absent / URL /
value.

## 2. LangGraph agent with Playwright MCP (three lines change)

```python
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.prebuilt import create_react_agent
from lite_locator.langchain import with_lite_locator, LOCATOR_HINT, snapshot_hook

tools = with_lite_locator(await load_mcp_tools(session), lean=True)   # ★1 find tool, change reports, stable code
agent = create_react_agent(llm, tools,
                           prompt=LOCATOR_HINT + your_prompt,          # ★2 find first; full snapshot only when needed
                           pre_model_hook=snapshot_hook)               # ★3 old full snapshots are not re-read
```

What the LLM then sees (real output, practicesoftwaretesting.com, Playwright MCP 0.0.83):

```
browser_find_element(step="Check the Hammer category filter")
  - checkbox "Hammer" [ref=e213]  (section: By category:)
  - img "Hammer" [ref=e384]  (section: Hammer)
  - heading "Hammer" [ref=e390]  (section: Combination Pliers)
  (3 of 126 elements, ~40 tokens instead of ~3324 for the full page.)

browser_click(target="e213")  ->
  ### Ran Playwright code
  await page.getByLabel("Hammer", { exact: true }).click();      <- Playwright MCP recorded [data-test="category-01M672…"]
  ### Result
  Same page. Changes:
  ~ checkbox "Hammer"  ->  checkbox "Hammer" [checked] [ref=e213]
  - link "Combination Pliers … $14.15"
  ...
```

Options: `diffs=False` keeps the action tools unchanged; `keep={"browser_navigate", "browser_click", ...}` shows the
LLM only those tools (tool definitions are re-sent on every call) while the library still uses the others it needs;
`stable_code=False` keeps Playwright MCP's recorded locators as they are; `lean=True` drops tool options a test agent
doesn't need (`doubleClick`, `modifiers`, `slowly`): fewer tokens per call, and typing replaces a field's value instead
of appending to it. Your Playwright MCP server, its version and flags stay as they are. Full examples:
[`examples/quickstart_langgraph.py`](examples/quickstart_langgraph.py), [`examples/agent_testgen.py`](examples/agent_testgen.py).

## 3. Your own Python code with a Playwright page

```python
from lite_locator import Locator, act

loc = Locator()                                          # loads the models once (~2 s)
hits = loc.find(page, "Click the product Combination Pliers")
hits.prompt        # the shortlist as text for your LLM prompt (it picks one)
hits[0]["py"]      # 'page.locator("a").filter(has=page.get_by_role("heading", name="Combination Pliers", exact=True))'
hits.tokens, hits.full_tokens                            # ~165 vs ~3382

print(act(page, hits[0], "click"))
# Navigated to a new page. Page: Combination Pliers - Practice Software Testing - Toolshop - v5.0  .../product/...
print(act(page, loc.find(page, "Click the Add to cart button")[0], "click"))
# Same page. Changes: + text: 1  + alert "Product added to shopping cart."
```

`act(page, hit, action, value)` actions: `click`, `dblclick`, `hover`, `fill`, `type`, `select`, `check`, `uncheck`,
`press`. Async pages: `await loc.afind(...)`, `await aact(...)`. `to_locator(page, hit)` gives a live Playwright
Locator. Full example: [`examples/quickstart_page.py`](examples/quickstart_page.py).

## 4. MCP client (proxy in front of Playwright MCP)

```json
{"mcpServers": {"playwright": {"command": "lite-locator-mcp", "args": ["--", "--headless", "--isolated"]}}}
```

All Playwright MCP tools stay available; `browser_find_element(step)` is added and actions return what changed.
Arguments after `--` go to Playwright MCP. Options: `--lam 0.005|0.01|0.02` (more / default / fewer candidates),
`--full-snapshots` (actions return Playwright's own full snapshot), `--lean` (start Playwright MCP with
`--snapshot-mode none`). `PLAYWRIGHT_MCP` overrides the child command (default `npx -y @playwright/mcp@0.0.83`).

## How "what changed" behaves

| After the action | The LLM gets |
|---|---|
| A new page | `Navigated to a new page.` + title, URL, main headings, what can be done there |
| Part of the page changed | `Same page. Changes:` + `+` added, `-` removed, `~` changed elements, with the card (product) for repeated buttons |
| Nothing changed, even after waiting up to 3 s for slow responses | `No change detected within 3 s. If this action submits, pays or deletes something, do not repeat it: check the page with browser_snapshot first.` |
| A new tab or a browser dialog | `A new tab opened: …` / `A browser dialog appeared: '…' (it was dismissed)`; `act(..., dialog="accept")` to accept |

Changes that happen by themselves (tickers, carousels, timers) are filtered out: the page is compared with itself just
before the action.

## Limits

- Checks over a whole page or list (counts, "all items", sorted order): TestRunner checks texts, URLs and single
  values; an agent should use `browser_snapshot` for these (the hint says so).
- The right element is outside the shortlist for roughly 10–18% of hard steps; TestRunner then fails the step (the
  healer retries with the LLM choosing), an agent takes the full snapshot.
- English pages only (the models are English).
- Visual-only changes (colours, canvas charts) are invisible in accessibility snapshots.
- Test cases still need a human review: a wrong expectation in a test case is reported as a failure, as it should be.

## Development and release checks

Every change is recorded in [CHANGELOG.md](CHANGELOG.md) (what, why, how it was checked). `ci/check.sh` runs lint
(ruff), a security scan (bandit), a secrets scan, a dependency vulnerability audit (pip-audit), the package build and
metadata checks, the test suite on local test pages in three environments (proxy with mcp 2.x, LangChain with the
latest adapters, and next to mcp 1.3), and a ranking quality gate against a recorded baseline. The report goes to
`ci/reports/<version>.md`; `ci/publish.sh` uploads only an all-green report of the current commit.

The training code (data collection, model training and evaluation) is not part of this repository; the package
ships the trained models.

## Credits and licences

- Code: MIT (see LICENSE).
- Retriever fine-tuned from [BAAI/bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5) (MIT).
- Reranker fine-tuned from [cross-encoder/ms-marco-MiniLM-L6-v2](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2) (Apache-2.0).
- Training data includes [Mind2Web](https://osu-nlp-group.github.io/Mind2Web/) (Deng et al., 2023, CC BY 4.0) and
  pages from public websites.
