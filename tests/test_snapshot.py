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


def test_dropdown_options_are_part_of_its_text(loc):
    snap = """- main [ref=e1]:
  - paragraph [ref=e2]: 4 products found for 'pliers'
  - combobox "sort" [ref=e3]:
    - option "Name (A - Z)"
    - option "Price (High - Low)"
    - option "Price (Low - High)"
  - checkbox "Measures" [ref=e4]
  - button "Compare" [ref=e5]
"""
    els = {e["ref"]: e for e in elements(snap)}
    assert "Price (Low - High)" in els["e3"]["text"]
    assert loc.find(snap, "Sort the results by price, low to high")[0]["ref"] == "e3"


def test_yaml_quoted_node_lines_are_parsed():
    snap = """- main [ref=e1]:
  - 'link "Combination Pliers Compare CO₂: A B C $14.15" [ref=e196] [cursor=pointer]':
    - /url: /product/1
    - img "Combination Pliers" [ref=e198]
  - 'button "It''s: here" [ref=e5]'
"""
    els = {e["ref"]: e for e in elements(snap)}
    assert els["e196"]["role"] == "link" and els["e196"]["name"].startswith("Combination Pliers")
    assert els["e196"]["attrs"].get("href") == "/product/1"
    assert els["e5"]["name"] == "It's: here"


RESULTS = """- main [ref=e1]:
  - heading "4 products found" [level=1] [ref=e2]
  - generic [ref=e3]:
    - generic [ref=e10]:
      - link "Long Nose Pliers $14.24" [ref=e11]
    - generic [ref=e20]:
      - link "Slip Joint Pliers $9.17" [ref=e21]
    - generic [ref=e30]:
      - link "Combination Pliers $14.15" [ref=e31]
    - generic [ref=e40]:
      - link "Pliers $12.01" [ref=e41]
  - textbox "First name" [ref=e50]
"""


def test_ordinal_steps_pick_by_position(loc):
    assert loc.find(RESULTS, "Open the first product in the results")[0]["ref"] == "e11"
    assert loc.find(RESULTS, "click the last product")[0]["ref"] == "e41"
    assert loc.find(RESULTS, "Enter first name")[0]["ref"] == "e50"


UNNAMED = """- main [ref=e1]:
  - heading "Slip Joint Pliers" [level=1] [ref=e2]
  - button "Increase quantity" [ref=e3]
  - button [ref=e4] [cursor=pointer]
  - button [ref=e5] [cursor=pointer]
"""
DETAILS = {"button": [{"testid": "increase-quantity", "text": "+"}, {"testid": "add-to-cart", "id": "btn-add-to-cart"},
                      {"testid": "add-to-favorites", "icon": "icon-heart"}]}


def test_unnamed_buttons_described_from_dom(loc):
    from elem_rank.snapshot import merge_details, needs_details
    els = elements(UNNAMED)
    assert needs_details(els)
    by = {e["ref"]: e for e in merge_details(els, DETAILS)}
    assert by["e4"]["text"] == "add to cart btn add to cart" and by["e3"]["name"] == "Increase quantity"
    assert loc.find(UNNAMED, "Add the product to the cart", details=DETAILS)[0]["ref"] == "e4"


def test_unnamed_details_skipped_when_counts_differ():
    from elem_rank.snapshot import merge_details
    by = {e["ref"]: e for e in merge_details(elements(UNNAMED), {"button": DETAILS["button"][:2]})}
    assert by["e4"]["text"] == "" and by["e5"]["text"] == ""
