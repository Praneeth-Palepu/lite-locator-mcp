# Changelog: lite-locator-mcp

Every release: what changed, why, and how it was checked. Reports with the full check output are in `ci/reports/`.

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
