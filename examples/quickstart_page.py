"""Quickstart 1: lite-locator inside your own Python code (Playwright page, no MCP, no LLM needed to try it).

    pip install lite-locator-mcp playwright
    python quickstart_page.py

For every test step: find() gives a short list of candidate elements (instead of the whole page), and act() performs
the action and tells you what changed (instead of a new full snapshot). In a real agent, the LLM picks from hits.prompt.
"""
from playwright.sync_api import sync_playwright

from lite_locator import Locator, act

STEPS = [  # (test step in plain words, action, value)
    ("Enter standard_user in the username field", "fill", "standard_user"),
    ("Enter the password", "fill", "secret_sauce"),
    ("Click the login button", "click", None),
    ("Add the Sauce Labs Backpack to the cart", "click", None),
    ("Open the shopping cart", "click", None),
]

loc = Locator()  # loads the bundled models once (~2 s)

with sync_playwright() as pw:
    page = pw.chromium.launch().new_page()
    page.goto("https://www.saucedemo.com/")

    for step, action, value in STEPS:
        hits = loc.find(page, step)  # 1. shortlist for this step
        print(f"\nSTEP: {step}")
        print(f"  candidates sent to the LLM: {len(hits)} (~{hits.tokens} tokens instead of ~{hits.full_tokens})")
        best = hits[0]  # 2. in an agent, the LLM chooses from hits.prompt
        print(f"  chosen: {best['py']}")
        print("  result:", act(page, best, action, value).replace("\n", "\n          "))  # 3. what changed
