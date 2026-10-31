"""Locator strings: correct preference order, and safe quoting of page text (page content is untrusted)."""
import ast

from elem_rank.locate import locator, py_locator

HOSTILE = 'x"); import os; os.system("echo pwned"); ("\\\n'


def test_preference_order():
    assert locator({"attrs": {"testid": "u", "testid_attr": "data-testid"}}) == 'page.getByTestId("u")'
    assert locator({"attrs": {"testid": "u", "testid_attr": "data-test"}}) == "page.locator('[data-test=\"u\"]')"
    # exact, like the Python locator: "Cart" must not also match "Add to cart"
    assert locator({"role": "link", "name": "Cart", "attrs": {}}) == 'page.getByRole("link", { name: "Cart", exact: true })'
    assert locator({"attrs": {"placeholder": "Email"}}) == 'page.getByPlaceholder("Email", { exact: true })'


def test_python_locator_variants():
    e = {"role": "button", "name": "Add to cart", "attrs": {}, "card": "Backpack", "card_sel": "div.item"}
    assert py_locator(e) == 'page.get_by_role("button", name="Add to cart", exact=True)'
    assert py_locator(e, how="card-scoped").startswith('page.locator("div.item").filter(has_text="Backpack")')
    assert py_locator(e, js='page.getByRole("button").nth(2)', how="positional (brittle)").endswith(".nth(2)")


def test_hostile_text_stays_a_string_literal():
    for e in ({"role": "button", "name": HOSTILE, "attrs": {}}, {"kind": "text", "text": HOSTILE, "attrs": {}},
              {"attrs": {"placeholder": HOSTILE}}, {"attrs": {"testid": HOSTILE, "testid_attr": "data-test"}},
              {"attrs": {}, "css": HOSTILE}):
        expr = ast.parse(py_locator(e), mode="eval").body
        assert isinstance(expr, ast.Call)  # one call, nothing else
        args = [a for a in expr.args] + [k.value for k in expr.keywords]
        assert all(isinstance(a, ast.Constant) for a in args), py_locator(e)
