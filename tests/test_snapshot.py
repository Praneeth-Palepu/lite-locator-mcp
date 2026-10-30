"""Parsing Playwright MCP snapshots into candidates (no browser, no models)."""
from elem_rank.snapshot import ORPHAN_PENALTY, demote_orphans, elements, line, parse_tree

SNAP = '''- Page URL: http://x/
- Page Title: Shop
```yaml
- banner [ref=e1]:
  - generic [ref=e2]:
    - text: English
- main [ref=e3]:
  - textbox "Username" [ref=e4]
  - button "Login" [ref=e5] [cursor=pointer]
  - generic [ref=e6]:
    - heading "Backpack" [level=3] [ref=e7]
    - button "Add to cart" [ref=e8]
  - generic [ref=e9]:
    - heading "Onesie" [level=3] [ref=e10]
    - button "Add to cart" [ref=e11]
  - generic [ref=e12]: "\\"2026 Guidelines\\" See details"
```'''


def test_parse_and_refs():
    els = elements(SNAP)
    refs = {e["ref"]: e for e in els}
    assert refs["e4"]["role"] == "textbox" and refs["e4"]["name"] == "Username"
    assert refs["e5"]["landmark"].startswith("main")


def test_quoted_text_is_decoded():
    assert any(e["text"].startswith('"2026 Guidelines"') for e in elements(SNAP))


def test_card_context_for_repeated_labels():
    cards = {e["ref"]: e.get("card") for e in elements(SNAP) if e["name"] == "Add to cart"}
    assert cards == {"e8": "Backpack", "e11": "Onesie"}


def test_plain_text_offered_through_container():
    els = elements(SNAP)
    eng = [e for e in els if e["text"] == "English"]
    assert eng and eng[0]["ref"] == "e2"  # clickable through the container that holds it
    assert "plain text" in line(eng[0])


def test_orphan_penalty():
    els = [{"orphan": True}, {}]
    s = demote_orphans([1.0, 1.0], els)
    assert s[0] == 1.0 - ORPHAN_PENALTY and s[1] == 1.0


def test_tree_nesting():
    root = parse_tree(SNAP)
    main = next(n for n in root.walk() if n.role == "main")
    assert any(n.name == "Login" for n in main.walk())
