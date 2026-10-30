"""Page API on local pages: find() picks the right element, locators work live, act() reports the change."""
from lite_locator import act
from elem_rank.locate import to_locator


def test_login_fields_and_button(page, site, loc):
    page.goto(site + "/login.html")
    top = loc.find(page, "Enter standard_user in the username field")[0]
    assert top["locator"] == "page.locator('[data-test=\"username\"]')"
    assert "password" in loc.find(page, "type the password")[0]["locator"]
    assert "login" in loc.find(page, "click the login button")[0]["locator"]


def test_value_in_step_does_not_beat_the_field(page, site, loc):
    page.goto(site + "/login.html")  # the page also shows the text "standard_user"
    assert loc.find(page, "Enter standard_user in username")[0]["role"] == "textbox"


def test_repeated_buttons_get_card_scoped_locators(page, site, loc):
    page.goto(site + "/grid.html")
    h = loc.find(page, "Add the Sauce Labs Bike Light to the cart")[0]
    assert h["card"] == "Sauce Labs Bike Light" and h["matches"] == 1
    assert h["strategy"] in ("card-scoped", "positional (brittle)")


def test_js_only_control_is_found_and_works(page, site, loc):
    page.goto(site + "/jsonly.html")
    h = loc.find(page, "click on language options")[0]
    assert h["name"] == "English" or "English" in h["doc"]
    assert h["clickable"] is True
    out = act(page, h, "click")
    assert "Choose language" in out or "Other Languages" in out


def test_act_reports_fill_alert_and_navigation(page, site, loc):
    page.goto(site + "/login.html")
    assert act(page, loc.find(page, "username field")[0], "fill", "bob").startswith("Same page")
    out = act(page, loc.find(page, "click the login button")[0], "click")
    assert "+ alert" in out and "do not match" in out
    act(page, loc.find(page, "username field")[0], "fill", "standard_user")
    act(page, loc.find(page, "password field")[0], "fill", "secret")
    out = act(page, loc.find(page, "click the login button")[0], "click")
    assert out.startswith("Navigated") and "grid.html" in out


def test_snapshot_text_input(loc):
    snap = '- main [ref=e1]:\n  - textbox "Username" [ref=e2]\n  - button "Login" [ref=e3]\n'
    assert loc.find(snap, "enter the username")[0]["ref"] == "e2"


def test_tokens_are_reported(page, site, loc):
    page.goto(site + "/grid.html")
    h = loc.find(page, "open the cart")
    assert 0 < h.tokens <= h.full_tokens and h.total > len(h) - 1


def test_slow_response_is_waited_for(page, site, loc):
    page.goto(site + "/slow.html")  # the page answers 1.5 s after the click
    out = act(page, loc.find(page, "click pay now")[0], "click")
    assert "Payment received" in out and "No change" not in out


def test_no_change_warns_against_repeating(page, site, loc):
    page.goto(site + "/login.html")
    out = act(page, loc.find(page, "click Help")[0], "hover", wait_max=1)
    assert out.startswith("No change detected") and "do not repeat" in out


def test_new_tab_is_reported(page, site, loc):
    page.goto(site + "/popup.html")
    out = act(page, loc.find(page, "open products in a new tab")[0], "click")
    assert "A new tab opened" in out and "grid.html" in out


def test_dialog_is_reported_and_dismissed_by_default(page, site, loc):
    page.goto(site + "/popup.html")
    out = act(page, loc.find(page, "click delete item")[0], "click", wait_max=1)
    assert "Delete this item?" in out and "dismissed" in out and "Deleted" not in out


def test_dialog_can_be_accepted(page, site, loc):
    page.goto(site + "/popup.html")
    out = act(page, loc.find(page, "click delete item")[0], "click", dialog="accept")
    assert "accepted" in out and "Deleted" in out


def test_busy_page_changes_are_filtered(page, site, loc):
    page.goto(site + "/carousel.html")  # a ticker changes every 200 ms
    out = act(page, loc.find(page, "click subscribe")[0], "click")
    assert "Thanks for subscribing" in out and "Headline" not in out


def test_change_lines_name_the_card(page, site, loc):
    page.goto(site + "/grid.html")
    out = act(page, loc.find(page, "Add the Sauce Labs Onesie to the cart")[0], "click")
    assert "Remove" in out and "card: Sauce Labs Onesie" in out


def test_to_locator_builds_live_locators_without_eval(page, site, loc):
    page.goto(site + "/grid.html")
    h = loc.find(page, "Add the Sauce Labs Backpack to the cart")[0]
    assert to_locator(page, h).count() == 1
    assert to_locator(page, page.locator("#cart")).count() == 1  # a Locator passes through


def test_hint_covers_whole_page_checks_and_no_repeat():
    from lite_locator.langchain import LOCATOR_HINT
    assert "whole page" in LOCATOR_HINT and "do not repeat" in LOCATOR_HINT


def test_search_step_finds_the_search_box_not_the_value(page, site, loc):
    page.goto(site + "/search.html")  # "pliers" is also a category, an image and a product name here
    for step in ("Search for pliers", "Type pliers in the search box"):
        top = loc.find(page, step)[0]
        assert top["role"] in ("textbox", "searchbox") and "search" in top["locator"].lower(), (step, top["doc"])
    out = act(page, loc.find(page, "Search for pliers")[0], "fill", "pliers")
    assert "pliers" in out


def test_set_quantity(page, site, loc):
    page.goto(site + "/search.html")
    assert loc.find(page, "Set the quantity to 2")[0]["name"] == "Quantity"


def test_late_list_update_after_a_change_is_included(page, site, loc):
    page.goto(site + "/sort.html")  # selecting changes the dropdown at once and re-orders the list 0.7 s later
    out = act(page, loc.find(page, "Sort the results by price, low to high")[0], "select", "Price (Low - High)")
    assert "Price (Low - High)" in out and "Combination pliers" in out


def test_ordinal_step_on_a_page(page, site, loc):
    page.goto(site + "/results.html")
    assert "Long Nose Pliers" in loc.find(page, "Open the first product")[0]["name"]
    assert "$12.01" in loc.find(page, "open the last product")[0]["doc"]
    assert loc.find(page, "Enter first name")[0]["role"] == "textbox"
