"""Page API on local pages: find() picks the right element, locators work live, act() reports the change."""
from lite_locator import act


def test_login_fields_and_button(page, site, loc):
    page.goto(site + "/login.html")
    assert loc.find(page, "Enter standard_user in the username field")[0]["locator"] == "page.locator('[data-test=\"username\"]')"
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
