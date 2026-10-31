"""Plan-once runner and stable locators on a local shop page (volatile product ids, look-alike product names, a late
cart update with a lingering toast, icon-only delete buttons in a table). The LLM is a scripted fake."""
import json
import re

import pytest

from lite_locator.runner import TestRunner
from lite_locator.stable import needs_stable, stable_for

VOLATILE_ID = re.compile(r"01M66[0-9A-Z]{21}")


class FakeLLM:
    """Plans from a fixed list; answers element choices with `choice`. Records every prompt."""

    def __init__(self, steps, choice="1"):
        self.steps, self.choice, self.prompts = steps, choice, []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return json.dumps({"steps": self.steps}) if prompt.startswith("Convert") else self.choice


CART_CASE = [
    {"step": "Open {site}/shop.html", "op": "open", "value": "{site}/shop.html"},
    {"step": "Click the product Claw Hammer", "op": "click", "target": "product Claw Hammer"},
    {"step": 'Verify the text "Product added to shopping cart." is shown', "op": "verify_text",
     "value": "Product added to shopping cart."},
    {"step": "Click the product Combination Pliers", "op": "click", "target": "product Combination Pliers"},
    {"step": "Click the cart link in the header", "op": "click", "target": "cart link"},
    {"step": "Verify the cart lists Claw Hammer", "op": "verify_text", "value": "Claw Hammer"},
    {"step": "Click the delete button for Claw Hammer", "op": "click", "target": "delete button for Claw Hammer"},
    {"step": "Verify Claw Hammer is no longer listed in the cart", "op": "verify_absent", "value": "Claw Hammer"},
]


def _steps(site):
    return [{k: v.format(site=site) for k, v in s.items()} for s in CART_CASE]


def test_needs_stable():
    assert needs_stable("""page.locator('[data-test="product-01M66ZGQB3VBTR98C6YDM2R0N4"]')""")
    assert needs_stable('page.locator("div > ul > li:nth-of-type(3) > a")')
    assert not needs_stable("""page.locator('[data-test="add-to-cart"]')""")
    # a position the step asks for is kept
    assert not needs_stable("""page.locator('[data-test="product-name"]').nth(0)""", "Verify the first product is X")
    assert needs_stable("""page.locator('[data-test="product-name"]').nth(1)""", "Click the product Claw Hammer")


def test_stable_locators_on_live_page(page, site):
    page.goto(f"{site}/shop.html")
    card = page.locator('[data-test="product-01M66ZGQAH1H4J5ASKENNY1YCT"]')
    js, py, lv = stable_for(page, card, """page.locator('[data-test="product-01M66ZGQAH1H4J5ASKENNY1YCT"]')""")
    assert "heading" in js and "Claw Hammer" in js and not VOLATILE_ID.search(js) and lv.count() == 1
    box = page.locator('[data-test="category-01M66ZGQ9KM34J20ZVQYPAMZ67"]')
    js, py, lv = stable_for(page, box, """page.locator('[data-test="category-01M66ZGQ9KM34J20ZVQYPAMZ67"]')""")
    assert js == 'page.getByLabel("Hammer", { exact: true })' and py == 'page.get_by_label("Hammer", exact=True)'


def test_runner_cart_flow(page, site):
    llm = FakeLLM(_steps(site))
    res = TestRunner(llm).run(page, "manual test case")
    assert res.passed, "\n".join(f"{x['op']} {x['ok']} {x['chosen_by']} {x['info'][:120]}" for x in res.log)
    code = "\n".join(res.lines)
    assert not VOLATILE_ID.search(code)                                   # no database ids in the script
    assert "Claw Hammer with Shock" not in code                           # the exact product, not the look-alike
    assert "toHaveText(\"1\")" in code                                    # waits for the late badge update
    assert "getByRole('row').filter({ hasText: \"Claw Hammer\" })" in code  # icon-only delete button by its row
    assert res.usage["plan"]["calls"] == 1
    assert res.usage.get("choose", {}).get("calls", 0) <= 2               # the library chose most elements itself


def test_icon_button_described(loc, page, site):
    page.goto(f"{site}/shop.html")
    page.locator('[data-test="product-01M66ZGQAH1H4J5ASKENNY1YCT"]').click()
    page.wait_for_timeout(900)
    page.locator('[data-test="nav-cart"]').click()
    hits = loc.find(page, "Click the delete button for Claw Hammer")
    assert "icon=xmark" in hits[0]["doc"] and "row: Claw Hammer" in hits[0]["doc"]


def test_plan_values_come_from_the_case():
    garbled = [{"step": "Open https://practicesoftwaretesting.com", "op": "open", "value": "https://practicesoftware"},
               {"step": 'Verify the text "Searched for: pliers" is shown', "op": "verify_text", "value": "Searched pliers"},
               {"step": 'Verify the text "Product added to shopping cart." is shown', "op": "verify_text",
                "value": "Product added to shopping"},
               {"step": "Verify the first product name shown is Wood Saw", "op": "verify_text", "value": "Wood Saw"}]
    steps = TestRunner(FakeLLM(garbled)).plan("case")
    assert steps[0]["value"] == "https://practicesoftwaretesting.com"
    assert steps[1]["value"] == "Searched for: pliers"
    assert steps[2]["value"] == "Product added to shopping cart."      # cut-off copy of the quoted message
    assert steps[3]["op"] == "verify_value"          # a position in the step: check that element, not any text


def test_verify_failure_is_reported_not_hidden(page, site):
    steps = [{"step": f"Open {site}/shop.html", "op": "open", "value": f"{site}/shop.html"},
             {"step": "Verify the product Thor Hammer is listed", "op": "verify_text", "value": "Thor Hammer"}]
    res = TestRunner(FakeLLM(steps), wait=1).run(page, "case")
    assert not res.passed and res.log[-1]["info"].startswith("text not visible")


@pytest.mark.parametrize("answer", ["0", "no idea"])
def test_llm_refusal_fails_the_step(page, site, answer):
    steps = [{"step": f"Open {site}/shop.html", "op": "open", "value": f"{site}/shop.html"},
             {"step": "Click the gift card button", "op": "click", "target": "gift card button"}]
    res = TestRunner(FakeLLM(steps, choice=answer), strict=True).run(page, "case")
    assert not res.passed and res.log[-1]["chosen_by"] == "llm"


def test_plan_tolerates_chatty_models():
    class Chatty:
        def __call__(self, prompt):
            return ('Here is the plan:\n{"steps": [{"op": "open", "value": "https://example.com"}, '
                    '{"op": "click", "target": "login button"}]}\nLet me know if you need more.')
    steps = TestRunner(Chatty()).plan("Title: x\n1. Open https://example.com\n2. Click the login button")
    assert [s["step"] for s in steps] == ["Open https://example.com", "Click the login button"]


def test_find_returns_stable_locators(loc, page, site):
    from elem_rank.locate import to_locator
    page.goto(f"{site}/shop.html")
    hits = loc.find(page, "Check the Hammer category filter")
    top = hits[0]
    assert not VOLATILE_ID.search(top["locator"]) and not VOLATILE_ID.search(top["py"])
    assert to_locator(page, top).count() == 1


def test_garbled_values_are_repaired_from_the_case():
    from elem_rank.runner import _respan
    assert _respan("Slip Joint P's", "Verify the cart lists Slip Joint Pliers") == "Slip Joint Pliers"
    assert _respan("$28. 30", "Verify the line total $28.30 is shown") == "$28.30"
    assert _respan("Gift card", "Click the login button") == "Gift card"      # not in the case: left as is


def test_value_next_to_its_label(page):
    page.set_content("<main><h1>Cart</h1><table><tr><td>Total</td><td>$22.96</td></tr></table></main>")
    steps = [{"step": "Verify the cart total shows $22.96", "op": "verify_value", "target": "cart total",
              "value": "$22.96"}]
    res = TestRunner(FakeLLM(steps)).run(page, "case", steps=steps)
    assert res.passed, res.log
    wrong = [{"step": "Verify the cart total shows $99.00", "op": "verify_value", "target": "cart total",
              "value": "$99.00"}]
    assert not TestRunner(FakeLLM(wrong)).run(page, "case", steps=wrong).passed     # no false pass


def test_missing_verify_value_is_taken_from_the_step(page):
    plan = [{"step": "Verify the cart lists Combination Pliers", "op": "verify_text", "target": "Combination Pliers"},
            {"step": "Verify something", "op": "verify_text"}]
    r = TestRunner(FakeLLM(plan))
    steps = r.plan("case")
    assert steps[0]["value"] == "Combination Pliers"
    page.set_content("<p>Combination Pliers</p>")
    res = r.run(page, "case", steps=steps)
    assert res.log[0]["ok"] and not res.log[1]["ok"] and "no expected value" in res.log[1]["info"]
