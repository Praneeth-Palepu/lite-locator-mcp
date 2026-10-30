"""What an action changed (no browser)."""
from elem_rank.diff import MAX_LINES, Snap, changes, outcome, split_snapshot

A = '- main:\n  - textbox "Username"\n  - button "Login"\n'


def test_no_change():
    assert outcome(Snap("u", "t", A), Snap("u", "t", A)).startswith("No visible change")


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
