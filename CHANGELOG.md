# Changelog: lite-locator-mcp

Every release: what changed, why, and how it was checked. Reports with the full check output are in `ci/reports/`.

## 0.1.4 (2026-10-30)
**What:**
- Actions: when nothing changes, keep checking for up to 3 s (slow server responses) before reporting; the "no
  change" message now says not to repeat a submit / payment / delete and to check with browser_snapshot. New tabs and
  browser dialogs are reported (`act(..., dialog="dismiss"|"accept")`).
- Change lines name the card for repeated buttons ("+ button Remove (card: Sauce Labs Backpack)").
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
**Checked:** SauceDemo 13-step checkout, tokens re-read across LLM calls: plain Playwright MCP 220,771 → 29,119
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
