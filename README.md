# lite-locator-mcp

Fewer tokens for Playwright test agents, without replacing the LLM. Two ideas:

1. **Shortlist instead of the whole page.** For each test step ("Add the Backpack to the cart"), small local models
   rank the page's elements and the LLM sees only the best few, with ready Playwright locators or MCP refs. If none
   fits, the LLM can still ask for the full accessibility tree.
2. **What changed instead of a new snapshot.** After a click or fill, the LLM gets the outcome in a few lines
   (navigated, the elements that appeared / disappeared / changed, or no change), not a full snapshot that then
   sits in the history and is re-read on every LLM call.

Measured on a 13-step SauceDemo checkout, tokens the agent's LLM re-reads across all its calls: plain Playwright MCP
220,771 → 29,119 with both (−87%). On the hard held-out test set (38 sites never seen in training) the right element
is first 72% of the time and in the shortlist 82–90% of the time, at 150–500 tokens instead of ~12,000 for the page.

Runs on CPU, no GPU and no PyTorch (int8 ONNX models are inside the package, nothing is downloaded at runtime).

## Install: pick your setup

| You use | Install | Notes |
|---|---|---|
| Your own Python code with a Playwright page | `pip install lite-locator-mcp` | |
| LangChain / LangGraph with `langchain-mcp-adapters` | `pip install lite-locator-mcp` | Works with any `mcp` 1.x (tested with 1.3). **Don't add `[mcp]`**: `langchain-mcp-adapters` needs mcp 1.x. |
| An MCP client (Claude Code, Cursor, Copilot) | `pip install "lite-locator-mcp[mcp]"` | The proxy needs mcp ≥ 2.3; put it in its own venv if something else needs mcp 1.x. |

Needs Python 3.10+, and Node 18+ when you use Playwright MCP. A browser once: `python -m playwright install chromium`.
On Windows, `onnxruntime` needs the Microsoft Visual C++ 2015–2022 runtime (usually already installed).

## 1. LangGraph agent with Playwright MCP (three lines change)

```python
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.prebuilt import create_react_agent
from lite_locator.langchain import with_lite_locator, LOCATOR_HINT, snapshot_hook

tools = with_lite_locator(await load_mcp_tools(session))        # ★1 adds browser_find_element; actions return changes
agent = create_react_agent(llm, tools,
                           prompt=LOCATOR_HINT + your_prompt,    # ★2 find first; full snapshot only when needed
                           pre_model_hook=snapshot_hook)         # ★3 old full snapshots are not re-read
```

What the LLM then sees:

```
browser_find_element(step="Add the Sauce Labs Backpack to the cart")
  - button "Add to cart" [ref=e54]  (card: Sauce Labs Backpack; in: main)
  - button "Add to cart" [ref=e102] (card: Sauce Labs Onesie; in: main)
  (2 of 47 elements, ~44 tokens instead of ~1039 for the full page.)

browser_click(ref="e54")  ->  ### Result
  Same page. Changes:
  + button "Cart, 1 items"
  - button "Cart, empty"
  + button "Remove"  (card: Sauce Labs Backpack)
  - button "Add to cart"  (card: Sauce Labs Backpack)
```

Your Playwright MCP server, its version and flags stay as they are. `with_lite_locator(tools, diffs=False)` keeps the
action tools unchanged. Full example: [`examples/quickstart_langgraph.py`](examples/quickstart_langgraph.py)
(Azure OpenAI or a local Ollama model).

## 2. Your own Python code with a Playwright page

```python
from lite_locator import Locator, act

loc = Locator()                                          # loads the models once (~2 s)
hits = loc.find(page, "Enter standard_user in the username field")
hits.prompt        # the shortlist as text for your LLM prompt (it picks one)
hits[0]["py"]      # 'page.locator("[data-test=\"username\"]")' : for generated test code
hits.tokens, hits.full_tokens                            # ~55 vs ~307

print(act(page, hits[0], "fill", "standard_user"))       # Same page. Changes: ~ textbox "Username" -> ...: standard_user
print(act(page, loc.find(page, "login button")[0], "click"))
# Navigated to a new page. Page: Swag Labs  .../inventory.html  Has: 20 buttons, 4 options, 3 links, 1 combobox
```

`act(page, hit, action, value)` actions: `click`, `dblclick`, `hover`, `fill`, `type`, `select`, `check`, `uncheck`,
`press`. Async pages: `await loc.afind(...)`, `await aact(...)`. `to_locator(page, hit)` gives a live Playwright
Locator. Full example: [`examples/quickstart_page.py`](examples/quickstart_page.py). Exploring a site and generating
pytest tests with an LLM: [`examples/explore_generate.py`](examples/explore_generate.py).

## 3. MCP client (proxy in front of Playwright MCP)

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

## Limits (when to use the full snapshot)

- Checks over a whole page or list (counts, "all items", totals): the shortlist shows only a few elements. The hint
  tells the LLM to use `browser_snapshot` for these.
- The right element is outside the shortlist for roughly 10–18% of hard steps; the LLM then sees nothing fits and
  takes the full snapshot (more tokens, still correct).
- English pages only (the models are English).
- Visual-only changes (colours, canvas charts) are invisible in accessibility snapshots, full or diff.

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
