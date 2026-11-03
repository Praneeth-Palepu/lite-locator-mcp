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



def test_new_elements_carry_their_ref_on_mcp_snapshots():
    before = '- combobox [ref=e141]\n'
    after = '- combobox "Maths" [expanded] [ref=e141]\n- listbox [ref=e300]:\n  - option "Maths" [ref=e301]\n'
    out = outcome(Snap("u", "t", before), Snap("u", "t", after))
    assert 'option "Maths" [ref=e301]' in out


def test_refs_do_not_create_fake_changes():
    a = '- button "Login" [ref=e5]\n'
    b = '- button "Login" [ref=e9]\n'  # same element, new ref after a re-render
    assert outcome(Snap("u", "t", a), Snap("u", "t", b)).startswith("No change detected")


# Negative tests need the page's feedback (errors, validation, confirmations): it must never be dropped.
ERR = '  - alert:\n    - text: Invalid username or password\n'


def test_navigation_summary_keeps_messages():
    out = outcome(Snap("http://x/a", "A", A), Snap("http://x/b?e=1", "B", A + ERR))
    assert out.startswith("Navigated") and "Message: Invalid username or password" in out


def test_new_view_keeps_messages():
    before = '- main:\n' + "".join(f'  - link "L{i}"\n' for i in range(30))
    after = '- main:\n' + "".join(f'  - button "B{i}"\n' for i in range(30)) + ERR
    out = outcome(Snap("u", "t", before), Snap("u", "t", after))
    assert "new view" in out and "Invalid username or password" in out


def test_capped_diff_keeps_every_message():
    big = '- main:\n' + "".join(f'  - link "L{i}"\n' for i in range(200))
    fields = "".join(f'  - textbox "Field {i}"\n  - text: Field {i} is required\n' for i in range(MAX_LINES))
    out = outcome(Snap("u", "t", big), Snap("u", "t", big + fields))
    assert all(f"Field {i} is required" in out for i in range(MAX_LINES)) and "more changes" in out


def test_noise_filter_never_hides_a_message():
    from elem_rank.diff import noise
    t0 = '- main:\n  - paragraph: Tip 1\n  - button "Send"\n'
    t1 = t0.replace("Tip 1", "Tip 2")
    t2 = t0.replace("Tip 1", "Error: message is required")
    lines, _ = changes(t1, t2, noise(t0, t1))
    assert any("message is required" in x for x in lines)


def test_long_message_is_kept_whole():
    msg = "Password must be at least 12 characters long and contain an upper case letter, a digit and a symbol " * 2
    out = outcome(Snap("u", "t", A), Snap("u", "t", A + f'  - text: {msg.strip()}\n'))
    assert msg.strip() in out


def test_message_gone_before_the_end_is_reported_as_brief():
    toast = A + '  - status: Draft saved\n'
    out = outcome(Snap("u", "t", A), Snap("u", "t", A), seen=[toast])
    assert "Shown briefly (gone now): Draft saved" in out and "No change" not in out


def test_validation_lines_and_eval_json():
    from elem_rank.diff import eval_json, validation_lines
    raw = '### Result\n"[{\\"field\\": \\"Email\\", \\"message\\": \\"Please fill out this field.\\"}]"\n### Ran'
    out = validation_lines(eval_json(raw))
    assert '"Email": Please fill out this field.' in out and validation_lines([]) == ""
