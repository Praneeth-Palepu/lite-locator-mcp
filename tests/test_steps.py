"""Step rewriting (rank the target element, not the typed value) and LLM argument repair."""
import pytest

from elem_rank.steps import clean_args, clean_ref, ordinal_of, target_of


@pytest.mark.parametrize("step,query", [
    ("Search for pliers", "search box"),
    ("Search pliers in the catalog", "search box"),
    ("Type pliers in the search box", "search box"),
    ("Enter standard_user in username", "username"),
    ("Type 'hello world' into the message box", "message box"),
    ("Fill the email field with a@b.com", "email field"),
    ("Set the quantity to 2", "quantity"),
    ("Change language to English", "language"),
    ("Select Price (Low - High) from the sort dropdown", "sort dropdown"),
    # not data entry: ranked as written
    ("Click the login button", "Click the login button"),
    ("Enter the password", "Enter the password"),
    ("Find the contact link", "Find the contact link"),
    ("Make sure the total is shown as $10", "Make sure the total is shown as $10"),
])
def test_target_of(step, query):
    assert target_of(step) == query


def test_clean_ref():
    assert clean_ref('checkbox "Pliers" [ref=e225]') == "e225"
    assert clean_ref("ref=e7") == "e7"
    assert clean_ref("e54") == "e54" and clean_ref("#login") == "#login" and clean_ref(None) is None


def test_clean_args():
    args = {"target": 'button "Go" [ref=e9]', "element": "Go"}
    assert clean_args("browser_click", args) == {"target": "e9", "element": "Go"}
    assert clean_args("browser_snapshot", {"filename": "x.yml"}) == {}



@pytest.mark.parametrize("step,pos", [
    ("Open the first product in the results", 0), ("click the 2nd result", 1), ("Select the last item", -1),
    ("Enter first name", None), ("Click the first name field", None), ("Open the product", None),
])
def test_ordinal_of(step, pos):
    assert ordinal_of(step) == pos
