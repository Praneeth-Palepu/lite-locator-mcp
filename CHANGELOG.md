# Changelog: lite-locator-mcp

Every release: what changed, why, and how it was checked. Reports with the full check output are in `ci/reports/`.

## 0.1.8 (2026-10-31)
**What:** documentation only. The README opens with what the library does and what it was measured to do (test cases
in any format, ≈ 1,000 tokens per test case, stable generated tests, results on two test suites), without
comparisons between versions; the version history stays in this file.
**Why:** owner: the package page should show the current capabilities, not version-to-version comparisons.
**Checked:** see `ci/reports/0.1.8.md` (full release checks; no code changes).

## 0.1.7.post1 (2026-10-31)
**What:** documentation only. The README's "Wins" section shows the 0.1.7 results (test cases as teams really write
them) next to the 0.1.6 cost results, with the ranges measured across all full runs instead of the best run.
**Why:** owner: the PyPI page still showed only the 0.1.6 wins after 0.1.7.
**Checked:** see `ci/reports/0.1.7.post1.md` (full release checks; no code changes).

## 0.1.7 (2026-10-31)
**Wins (measured):** test cases written the way teams really write them: 16 cases on demoblaze.com in four styles
("Steps | Expected Result" rows with preconditions, one-line prose, Gherkin, jargon such as PLP/PDP/ATC), one of them
with missing test data on purpose. 0.1.6 as published: 0/16. 0.1.7 with a local 4B model: 13/16 (13 of the 15 that can pass); with a stronger
model for the one planning call per case: 12/16 (12 of the 15 that can pass). Regression on the 0.1.6 suite
(practicesoftwaretesting.com, 34 cases): 33/34 plus all scripts passing; the one failure is a real change on the site (Bolt Cutters now shows under the Hammer + Pliers filters, which that test case says it must not).

**What** (all generic; each has a test on a local page):
- Opening a page waits for load with a time cap and goes on when a site never stops loading (polling, ads); the
  script does the same.
- Browser popups (alert / confirm) are accepted; their text is checked, and the script records and asserts it
  (`page.on('dialog')`, `expect.poll`). A click whose only effect is a popup or a new tab counts as a change.
- Implicit steps: when a step's element is not on the page (inside a dialog or menu, or on another page), the LLM picks
  one element to click first (at most twice per step); the extra click is recorded in the script.
- One sentence that becomes several steps ("send the message M with email E and name N") ranks each step by its own
  target, so each value goes to its own field. "Any" / "some" item without a name = the first one listed.
- Plans: test cases in any format (step/expected rows, Gherkin, prose); preconditions become setup steps; one action
  per step; values (URLs, expected texts) are checked against the whole test case, not only the step; a check of
  several quoted texts becomes one check per text; "open" without a full URL is a click; a step written as a function
  call is turned back into words; the example in the planner prompt uses made-up values (a small model copied real
  product names from it).
- Checks: text split across elements ("$360 <small>*includes tax</small>") is found; values that load late are looked
  for again; "select 12 as month" in a plain text field types it; "type" only considers text boxes.
- Scripts: role / placeholder / text locators are exact, as the Python locators already were ("Cart" no longer also
  matches "Add to cart"). A failed case gives a script marked `test.fixme` with the failing step as a comment, so an
  incomplete script never passes; the example reports it as "incomplete" and writes a per-step log
  (`<id>.log.json`).
- Examples cap the local model's reply length (`num_predict`): one runaway reply held a shared model for 40 minutes.
**Why:** owner asked whether real, messier test cases work, measured on a different site, with generic fixes only.
**Checked:** see `ci/reports/0.1.7.md`.

## 0.1.6 (2026-10-31)
**Wins (measured):** one user story on practicesoftwaretesting.com, 34 manual test cases, local Qwen3 4B.
LangGraph agent + Playwright MCP + lite-locator 0.1.5: 658 LLM calls, 2,528,293 input + 24,081 output tokens, 30/34
passing after healing, and the healed scripts broke again after the site's next data reset. `TestRunner` (0.1.6):
68 LLM calls, 22,413 input + 5,083 output tokens, 34/34 passing, all 34 generated scripts passing
`npx playwright test` (1 and 4 parallel workers, twice). At GPT-5.4 prices: ≈ $6.68 → ≈ $0.13 per user story.
A second full run: 33/34 (a value missing from one plan; now taken from the test case), 84 calls, 27,055 + 5,724
tokens.

**What:**
- `lite_locator.runner.TestRunner` (`elem_rank/runner.py`): plan once, the library executes. One LLM call turns a
  manual test case into steps; the library finds and acts on each element and asks the LLM (step + short list, ~300
  tokens) only when its top match is not clearly ahead, does not fit the action, or is not unique; an element whose
  own title is exactly what the step names is taken directly ("Claw Hammer", not "Claw Hammer with Shock Reduction
  Grip"; "delete button for Claw Hammer" names a button, not the product). A click that changes nothing goes back to
  the LLM. Verify steps (text / absent / URL / value) are checked in the browser by code; a value mismatch is shown to
  the LLM before the step fails; a label's value next to it ("Total  $22.96") is found without false passes; a toast
  that faded before the check counts when the previous action reported it. Values come from the test case text
  (garbled copies such as "$28. 30" or "Slip Joint P's" are repaired from it). `strict=True` = the LLM chooses
  every element (the healer). `Result.spec(title)` = the Playwright test.
- Stable locators (`elem_rank/stable.py`): locators with database IDs or positional CSS paths are replaced by label,
  card heading, table row or text, only when that matches exactly one element; in `Locator.find()` hits
  (`locator`, `py`, `to_locator`), in TestRunner scripts, and in the code Playwright MCP reports through
  `with_lite_locator(..., stable_code=True)` (default; one `browser_evaluate` per action). Positions the step asks
  for ("the first product") are kept.
- Generated scripts wait for what an action changed: the new page title after a navigation, the test-id element whose
  text changed (cart badge 1 → 2), and network idle after opening a page.
- Icon-only controls are extracted and described by their icon and table row (`icon=xmark (remove / delete / close)
  | class=btn btn-danger | row: Claw Hammer`); classes describe elements that have nothing else.
- `with_lite_locator(..., lean=True)`: drops `doubleClick`, `button`, `modifiers` (click) and `slowly` (type) from the
  tool definitions and calls. Fewer tokens per call, and typing replaces a field's value (with `slowly` a model typed
  "0" after the existing "1" and reported a false defect).
- Example `examples/run_test_cases.py` (suite runner with healer, script check and token report); examples and docs
  use practicesoftwaretesting.com.
**Why:** owner asked to cut the cost of generating and executing test cases without losing quality; a 34-case run
with the agent cost ≈ 2.55M tokens, mostly the conversation re-sent on every call and healing of scripts that used
database IDs.
**Checked:** see `ci/reports/0.1.6.md`; new tests on a local shop page (`tests/pages/shop.html`: re-seeded product IDs,
look-alike product names, a late cart update with a lingering toast, icon-only delete buttons in a table) for the
runner, stable locators, label/value checks, plan repairs, the wrapper's stable code and lean schemas.

## 0.1.5 (2026-10-30)
**What** (all found by a complex flow on practicesoftwaretesting.com: search → sort → first product → quantity → add to
cart → cart; each fix is generic and has a test on a local page):
- Data-entry steps are scored by their target element and as written, equally weighted (`elem_rank/steps.py`,
  `locate.rank_step`): "Search for pliers" picked the "Pliers" category checkbox instead of the search box. The first
  version ranked by the target alone; the quality gate showed 2 held-out steps losing first place (the value names a
  dropdown option, or tells look-alike fields apart), so both scores are combined.
- Ordinal steps ("open the first product", "click the last result") pick by position in the page's main repeated list
  of links; "first name" and similar field names are not positions.
- Parser: YAML-quoted snapshot lines (text containing ": ", e.g. "CO₂: A B C") were dropped, which hid every product
  card on the site.
- Dropdowns include their option labels, so "sort by price, low to high" finds the sort box on MCP snapshots.
- Unnamed buttons / links (icon buttons, "Add to cart" whose text is hidden from the accessibility tree) are described
  from their DOM attributes with one `browser_evaluate` call per page state; matched to the snapshot in page order,
  only when the counts agree exactly.
- After an action, the result waits until the page settles (a sort changes the dropdown at once and re-orders the
  list later); "new view" summaries only on pages with 20+ elements.
- Wrapper / proxy repair LLM arguments: a whole candidate line passed as a ref becomes the ref; `browser_snapshot`
  ignores `filename` so the tree stays inline.
- `with_lite_locator(tools, keep={...})`: the LLM sees only the named tools (fewer tokens per call) while the library
  still uses browser_snapshot / browser_evaluate. Filtering the list before wrapping hid those from the library.
- Hints: pass only the ref; describing the element to act on works best.
- Change reports show the ref of every added / changed element on MCP snapshots (`+ option "Maths" [ref=e301]`), so
  the LLM can pick an autocomplete suggestion or a dropdown option that just appeared. Found on demoqa.com: the
  suggestion was reported without a ref and the model guessed. Refs are not part of the comparison, so re-rendered
  elements with new refs don't show up as changes.
**Why:** owner asked for a complex use case on a different website, with the generated test script.
**Result on the site** (LangGraph agent, local Qwen3 4B): 0.1.4 stopped at step 2; 0.1.5 completed every step and
generated the script (search, sort, product, quantity, add to cart, open cart). The remaining errors were the small
model's (it clicked "increase quantity" twice although the result showed 1 → 2, then 2 → 3), and the change reports
made that visible.
**Second complex site** (owner request): demoqa.com practice form, 12 steps (text fields, radio, autocomplete,
checkbox, React state/city dropdowns, submit, confirmation dialog): PASS with the generated script after the ref fix.
**Trade-off (measured):** quality gate top-10 unchanged (81.67%); top-1 66.67% vs 67.50%: one held-out step ("Enter
"New York" in the Origin field") ranks 2nd behind the similar Destination field, still in the shortlist.
**Checked:** see `ci/reports/0.1.5.md`.

## 0.1.4 (2026-10-30)
**What:**
- Actions: when nothing changes, keep checking for up to 3 s (slow server responses) before reporting; the "no
  change" message now says not to repeat a submit / payment / delete and to check with browser_snapshot. New tabs and
  browser dialogs are reported (`act(..., dialog="dismiss"|"accept")`).
- Change lines name the card for repeated buttons ("+ button Remove (card: <product name>)").
- Changes that happen by themselves (tickers, carousels) are filtered: the page is compared with itself just before
  the action.
- Hint and tool descriptions: whole-page / whole-list checks use browser_snapshot; don't repeat irreversible actions.
- Security: removed `eval()` from the runtime (new `to_locator(page, hit)` builds Playwright Locators directly; find()
  hits carry the element); training code loads models with `torch.load(weights_only=True)` and pins Hugging Face
  revisions.
- Fixed: on MCP snapshots, short text inside an unnamed container ("English" in a generic) was dropped by two filters
  at once (0.1.2's JS-only control support did not cover that shape).
- Docs: README rewritten (setup table, three usage paths, behaviour table, limits). LangChain users must not install
  `[mcp]`: the latest langchain-mcp-adapters breaks with mcp 2.x (found by the new LangChain CI environment).
- Process: git history, test suite, `ci/check.sh` (17 checks) and `ci/publish.sh` (uploads only an all-green report
  of the current commit).
- Packaging: the training / research code (crawler, query generation, model training, evaluation, ONNX export) is no
  longer inside the package or the public repository; the wheel contains only the runtime. The `[train]` extra and
  the proxy's PyTorch fallback are gone (the shipped ONNX models are always used). Source repository linked on PyPI:
  https://github.com/Praneeth-Palepu/lite-locator-mcp
**Why:** the worst cases of "shortlist + what changed" (owner review, 2026-10-30): a late response reported as "no
change" could make the agent repeat a payment; a wrong look-alike pick was not visible in the diff; partial views
could pass a whole-page check; busy pages flooded the diff.
**Checked:** see `ci/reports/0.1.4.md`.

## 0.1.3 (2026-10-30)
**What:** actions return what changed on the page instead of a full snapshot (`elem_rank/diff.py`): a navigation
summary, the added (+) / removed (-) / changed (~) elements, or "No visible change". Page API `act()` / `aact()` /
`observe()`; LangChain `with_lite_locator(diffs=True)` wraps the Playwright action tools; `snapshot_hook`
(LangGraph pre_model_hook) hides superseded full snapshots; MCP proxy `--full-snapshots` to opt out.
**Why:** snapshots after every action pile up in the agent's history and every LLM call re-reads them.
**Checked:** a 13-step demo-shop checkout, tokens re-read across LLM calls: plain Playwright MCP 220,771 → 29,119
(−87%); LangGraph agent (local Qwen 4B) completed login → add to cart → verify cart on its own. Manual checks only
(no automated suite yet).

## 0.1.2 (2026-10-30)
**What:** JavaScript-only controls are found: on a Playwright page, text with a click listener on itself or a near
parent is marked clickable (Chromium DevTools protocol); on MCP snapshots, short text without its own ref is a
candidate through its container ref, with a score handicap (`ORPHAN_PENALTY`). Hits carry a Python locator (`py`).
Author changed to Praneeth Kumar Palepu (owner request), LICENSE updated to match.
**Why:** Shanghai Disney's language switcher is a plain `<div>English</div>` with a click handler; it was never a
candidate.
**Checked:** held-out crawl test (2,000 steps) after vs stored before: hit@1 78.95% → 78.85%, hard 52.91% → 52.80%
(without the handicap; the handicap only reduces this); Disney "click on language options" → "English" #1 and the
menu opened. The quick test caught a regression ("standard_user" hint text above the Username box) → handicap added.

## 0.1.1 (2026-10-29)
**What:** library API `Locator.find/afind` (Playwright page or snapshot text), LangChain tools `with_lite_locator`,
`page_tool`; MCP became an optional extra (`[mcp]`); `--snapshot-mode none` made opt-in (`--lean`).
**Why:** the owner's agents are Python LangGraph/LangChain on mcp 1.3; requiring mcp>=2.3 would break them.
**Checked:** fresh install next to mcp 1.3.0 (unchanged, `pip check` clean); sync and async page; LangChain with
Playwright MCP 0.0.29.

## 0.1.0 (2026-10-28)
**What:** first release: MCP proxy in front of Playwright MCP adding `browser_find_element`; fusion ranker
(fine-tuned bge-small + MiniLM) and RL cut policy, int8 ONNX, no PyTorch.
**Checked:** hard held-out test (38 sites): fusion hit@1 0.72, hit@10 0.87; int8 = PyTorch on 150 hard steps.
