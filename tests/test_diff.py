"""What an action changed (no browser)."""
from elem_rank.diff import MAX_LINES, Snap, changes, outcome, split_snapshot

A = '- main:\n  - textbox "Username"\n  - button "Login"\n'


def test_no_change():
    out = outcome(Snap("u", "t", A), Snap("u", "t", A))
    assert out.startswith("No change detected") and "do not repeat" in out


def test_navigation_summary():
    out = outcome(Snap("http://x/a", "A", A), Snap("http://x/b", "B", '- heading "Products" [level=1]\n- button "Add"\n'))
    assert out.startswith("Navigated") and "Products" in out and "1 button" in out


def test_added_element_inside_alert():
    after = A + '  - alert:\n    - text: Wrong password\n'
    out = outcome(Snap("u", "t", A), Snap("u", "t", after))
    assert "+ alert" in out and "Wrong password" in out


def test_changed_value_is_paired():
    after = A.replace('textbox "Username"', 'textbox "Username": bob')
    lines, _ = changes(A, after)
    assert lines == ['~ textbox "Username"  ->  textbox "Username": bob']


def test_hash_change_is_not_navigation():
    assert not outcome(Snap("http://x/#a", "t", A), Snap("http://x/#b", "t", A)).startswith("Navigated")


def test_long_diff_is_capped():
    after = A + "".join(f'  - button "B{i}"\n' for i in range(MAX_LINES + 10))
    big = '- main:\n' + "".join(f'  - link "L{i}"\n' for i in range(200))
    out = outcome(Snap("u", "t", big), Snap("u", "t", big + "".join(f'  - button "B{i}"\n' for i in range(MAX_LINES + 10))))
    assert "more changes" in out and after


def test_split_snapshot_both_formats():
    new = "### Page\n- Page URL: http://x/\n- Page Title: T\n### Snapshot\n```yaml\n- button \"A\" [ref=e1]\n```"
    old = "- Page URL: http://x/\n- Page Title: T\n- Page Snapshot\n```yaml\n- button \"A\" [ref=e1]\n```"
    for text in (new, old):
        rest, yaml, url, title = split_snapshot(text)
        assert url == "http://x/" and title == "T" and "button" in yaml and "```" not in rest


GRID = """- main:
  - generic:
    - heading "Backpack" [level=3]
    - button "Add to cart"
  - generic:
    - heading "Bike Light" [level=3]
    - button "Add to cart"
  - generic:
    - heading "Onesie" [level=3]
    - button "Add to cart"
"""


def test_changed_button_says_which_card():
    after = GRID.replace('- heading "Bike Light" [level=3]\n    - button "Add to cart"',
                         '- heading "Bike Light" [level=3]\n    - button "Remove"')
    out = outcome(Snap("u", "t", GRID), Snap("u", "t", after))
    assert '+ button "Remove"  (card: Bike Light)' in out and "(card: Bike Light)" in out.split("- button")[1]


def test_no_card_on_simple_pages():
    lines, _ = changes(A, A.replace('button "Login"', 'button "Log out"'))
    assert all("card:" not in x for x in lines)


def test_noise_positions_are_ignored():
    from elem_rank.diff import noise
    t0 = '- paragraph: Headline 1\n- main:\n  - button "Subscribe"\n'
    t1 = t0.replace("Headline 1", "Headline 2")
    t2 = t0.replace("Headline 1", "Headline 3") + '- status: Thanks for subscribing\n'
    lines, _ = changes(t1, t2, noise(t0, t1))
    assert lines == ['+ status "Thanks for subscribing"']


def test_new_tab_and_dialog_lines_come_first():
    out = outcome(Snap("u", "t", A), Snap("u", "t", A), extra=["A new tab opened: http://x/p"])
    assert out.splitlines()[0] == "A new tab opened: http://x/p"
