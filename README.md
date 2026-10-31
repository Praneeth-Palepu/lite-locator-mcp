# lite-locator-mcp

Fewer LLM tokens and sturdier generated tests for Playwright test automation. Small local models (CPU, inside the
package) find the page elements for each test step, so the LLM no longer reads whole pages or re-reads a growing
conversation.

## What it does

- **Runs manual test cases and writes the Playwright tests.** Give it test cases as your team writes them ("Steps |
  Expected Result" rows with preconditions, Gherkin, one-line prose, jargon such as PLP / PDP / ATC). One LLM call
  plans each case; the library finds and acts on the elements itself and asks the LLM a short question only when it
  is unsure. Preconditions become setup steps, a sentence with several actions becomes single steps, dialogs and menus
  the case doesn't mention are opened first, browser popups are checked, and "any item" means the first one listed.
- **About 1,000 LLM tokens per test case** (≈ $0.004 at GPT-5.4 prices, ≈ $0.15 for a 34-case user story), where an
  LLM agent loop used about 75,000 on the same cases, because the agent re-sends the whole conversation and page on
  every step.
- **Generated tests that keep passing.** No database IDs or positional CSS in the scripts: elements are located by
  label, card heading, table row or text, only when that matches exactly one element. Scripts wait for what the app
  changed (new page title, a badge going from 1 to 2, a popup) and pass with parallel workers. A test case that fails
  never produces a passing script: it is written as `test.fixme` with the failing step.
- **Works with small local models.** Measured with a 4B model on a laptop CPU/GPU:

| Test suite | Passed | Generated scripts passing `npx playwright test` |
|---|---|---|
| 34 written test cases for one user story (practicesoftwaretesting.com: search, filters, sorting, product page, quantity, cart) | 33–34 / 34 across runs | all passing cases |
| 16 messy test cases on another site (demoblaze.com: step/expected rows, prose, Gherkin, jargon; one has no test data on purpose) | 13 / 16 (13 of the 15 that can pass) | all passing cases but one |

- **Finds elements for LLM agents too.** For an existing LangGraph / Playwright MCP agent: a short list of candidate
  elements (40–150 tokens) instead of the whole page (2,000–12,000), what changed after an action instead of a new
  snapshot, and stable locators in the code Playwright MCP records. Icon-only buttons (✕, 🗑, +) are found by icon
  and table row.

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

The test case can be in any format your team uses (step / expected rows, Gherkin, prose). What it can't do: invent
data the case doesn't give ("log in with valid credentials"), so put the data in the test case.

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
