"""Exploration evidence for test-case writers (no browser)."""
from elem_rank.diff import Snap
from lite_locator.evidence import Recorder, check, parse_cases, render, writer_prompt

LOGIN = ('- textbox "Username" [ref=e1]\n- textbox "Password" [ref=e2]\n- button "Login" [ref=e3]\n')
FORM = ('- heading "Checkout: Your Information" [level=1]\n- textbox "First Name" [ref=e10]\n'
        '- textbox "Last Name" [ref=e11]\n- button "Continue" [ref=e12]\n')
ERR = '- alert:\n  - text: "Error: First Name is required"\n'
OVERVIEW = '- text: "Item total: $29.99"\n- text: "Tax: $2.40"\n- text: "Total: $32.39"\n- button "Finish" [ref=e20]\n'
DONE = '- heading "Thank you for your order!" [level=2]\n'


def explored():
    rec = Recorder()
    u = "https://shop.test/"
    rec.action("browser_navigate", {"url": u}, None, Snap(u, "Shop", LOGIN), "Navigated")
    rec.action("browser_type", {"ref": "e1", "text": "bob"}, Snap(u, "Shop", LOGIN),
               Snap(u, "Shop", LOGIN.replace('"Username" [ref=e1]', '"Username" [ref=e1]: bob')), "Same page")
    f = "https://shop.test/checkout"
    rec.action("browser_click", {"ref": "e3"}, Snap(u, "Shop", LOGIN), Snap(f, "Shop", FORM), "Navigated")
    rec.action("browser_click", {"ref": "e12"}, Snap(f, "Shop", FORM), Snap(f, "Shop", FORM + ERR), "Same page")
    filled = FORM.replace('"First Name" [ref=e10]', '"First Name" [ref=e10]: Ann').replace(
        '"Last Name" [ref=e11]', '"Last Name" [ref=e11]: Lee')
    rec.action("browser_type", {"ref": "e10", "text": "Ann"}, Snap(f, "Shop", FORM), Snap(f, "Shop", FORM), "Same page")
    rec.action("browser_type", {"ref": "e11", "text": "Lee"}, Snap(f, "Shop", FORM), Snap(f, "Shop", filled), "Same page")
    o = "https://shop.test/overview"
    rec.action("browser_click", {"ref": "e12"}, Snap(f, "Shop", filled), Snap(o, "Shop", OVERVIEW), "Navigated")
    rec.action("browser_click", {"ref": "e20"}, Snap(o, "Shop", OVERVIEW), Snap("https://shop.test/done", "Shop", DONE),
               "Navigated")
    return rec


def test_pack_has_fields_amounts_and_messages_with_their_cause():
    p = explored().pack()
    assert "First Name (textbox)" in p and "Total: $32.39" in p
    assert 'Click button "Continue"  → empty: First Name, Last Name; shows "Error: First Name is required"' in p
    assert len(p) < 2500


def test_observed_cases_repeat_the_path_and_expect_the_exact_message():
    cases = explored().observed_cases()
    neg = next(c for c in cases if "required" in c["expected"][0])
    assert neg["type"] == "negative" and neg["source"] == "observed"
    assert neg["steps"][0] == "Open https://shop.test/" and 'Type "bob" in textbox "Username"' in neg["steps"]
    assert "Leave First Name, Last Name empty" in neg["steps"] and neg["steps"][-1] == 'Click button "Continue"'
    pos = next(c for c in cases if "Thank you" in c["expected"][0])
    assert pos["type"] == "positive" and not any("required" in s for s in pos["steps"])


def test_check_flags_uncovered_criteria_and_invented_texts():
    rec = explored()
    cases = [{"ac": [1], "title": "t", "expected": ['"Error: First Name is required" is shown',
                                                    '"Your cart is empty" is shown',
                                                    '"Order cancelled" is shown (not observed)']}]
    out = check(cases, ["a", "b"], rec)
    assert out["uncovered"] == [2] and out["unverified"] == [("t", "Your cart is empty")]


def test_parse_render_and_prompt():
    cases = parse_cases('Here:\n```json\n[{"ac": [1], "title": "Login | works", "type": "positive", '
                        '"steps": ["Open"], "expected": ["ok"]}]\n```')
    assert cases[0]["title"] == "Login | works"
    assert "| TC01 | 1 | positive | Login / works |" in render(cases)
    assert render(cases, "csv").startswith("ID,AC,Type")
    rec = explored()
    p = writer_prompt(["Checkout works"], rec, rec.observed_cases())
    assert "1. Checkout works" in p and "do not repeat them" in p and "Exploration evidence" in p


def test_gaps_name_forms_never_submitted_with_a_field_empty():
    rec = explored()
    gaps = rec.gaps()   # the checkout form went through with First/Last Name filled; only an all-empty try was made
    # the empty try named only First Name: Last Name still needs its own try
    assert gaps == ['On /checkout: click button "Continue" with each of these left empty, one at a time (the others '
                    'filled): Last Name; note the exact message.']


def test_check_also_reads_single_quoted_texts_with_apostrophes():
    rec = explored()
    cases = [{"ac": [1], "title": "t", "expected": ["Error appears: 'Sorry, this user doesn't have access.'"]}]
    assert check(cases, ["a"], rec)["unverified"] == [("t", "Sorry, this user doesn't have access.")]


def test_parse_cases_keeps_complete_cases_of_a_cut_off_reply():
    cut = '[{"title": "a", "steps": ["x"], "expected": ["y"]}, {"title": "b", "steps": ["x"]}, {"title": "c", "ste'
    assert [c["title"] for c in parse_cases(cut)] == ["a", "b"]
    assert [c["title"] for c in parse_cases([{"type": "text", "text": "```json\n" + cut}])] == ["a", "b"]


def test_selector_targets_and_visited_urls():
    from elem_rank.evidence import _selector
    assert Recorder._target({"target": 'input[type="submit"]'}, None) == 'input[type="submit"]'
    assert Recorder._target({"target": "e12"}, None) == ""
    assert _selector('input[type="submit"]') and _selector("#login") and not _selector('button "Log in"')
    rec = explored()
    url = rec.origin + "/checkout"
    assert check([{"ac": [1], "title": "t", "expected": [f'The URL is "{url}"']}], ["x"], rec)["unverified"] == []


def test_merge_cases_tags_observed_cases_with_criteria():
    from lite_locator.evidence import merge_cases
    rec = explored()
    obs = rec.observed_cases()
    assert 'O1. ' in writer_prompt(["a", "b"], rec, obs) and '"observed": "O1"' in writer_prompt(["a"], rec, obs)
    written = parse_cases('[{"observed": "O1", "ac": [2]}, {"ac": [1], "title": "x", "steps": ["s"], "expected": []}]')
    cases = merge_cases(obs, written)
    assert cases[0]["ac"] == [2] and cases[0]["source"] == "observed" and cases[-1]["title"] == "x"
    assert len(cases) == len(obs) + 1 and check(cases, ["a", "b"], rec)["uncovered"] == []


class FakeShop:
    """A tiny browser for probe(): a login page and a checkout form that needs a signed-in session. Each call is fed
    to the recorder like with_lite_locator does. A new session (signed out) is the state probe() starts in."""
    def __init__(self, rec):
        self.rec, self.url, self.user, self.signed_in, self.vals = rec, "https://shop.test/", "", False, {}

    def snap(self, extra=""):
        if self.url.endswith("/checkout"):
            body = FORM
            for name, ref in (("First Name", "e10"), ("Last Name", "e11")):
                if self.vals.get(name):
                    body = body.replace(f'"{name}" [ref={ref}]', f'"{name}" [ref={ref}]: {self.vals[name]}')
            return Snap(self.url, "Shop", body + extra)
        body = LOGIN.replace('"Username" [ref=e1]', f'"Username" [ref=e1]: {self.user}') if self.user else LOGIN
        return Snap(self.url, "Shop", body)

    async def __call__(self, name, args):
        pre, extra = self.snap(), ""
        if name == "browser_navigate":
            self.url = args["url"] if self.signed_in or not args["url"].endswith("/checkout") else "https://shop.test/"
            self.vals = {}
        elif name == "browser_type":
            if args["ref"] == "e1":
                self.user = args["text"]
            else:
                self.vals[{"e10": "First Name", "e11": "Last Name"}[args["ref"]]] = args["text"]
        elif name == "browser_click" and args["ref"] == "e3" and self.user:
            self.signed_in, self.url = True, "https://shop.test/checkout"
        elif name == "browser_click" and args["ref"] == "e12":
            empty = [k for k in ("First Name", "Last Name") if not self.vals.get(k)]
            extra = f'- alert:\n  - text: "Error: {empty[0]} is required"\n' if empty else ""
        if name != "browser_snapshot":
            self.rec.action(name, args, pre, self.snap(extra), "Same page")
        else:
            self.rec.see(self.snap())
        return ""


def test_probe_signs_in_again_when_the_session_has_ended():
    import asyncio
    rec = explored()
    assert [f["untried"] for f in rec.forms()] == [["Last Name"]]
    log = asyncio.run(rec.probe(FakeShop(rec)))
    assert log == ["/checkout Last Name empty: Error: Last Name is required"]


def test_links_and_menu_items_are_not_form_submits():
    rec = Recorder()
    u = "https://shop.test/"
    page = '- textbox "Search" [ref=e1]: pliers\n- link "Slip Joint Pliers" [ref=e2]\n'
    rec.action("browser_click", {"ref": "e2"}, Snap(u, "Shop", page),
               Snap("https://shop.test/product/1", "Shop", '- heading "Slip Joint Pliers"\n'), "Navigated")
    assert rec.forms() == [] and rec.gaps() == []


def test_role_less_error_div_on_page_results_and_code_are_recorded():
    rec = Recorder()
    u = "https://shop.test/login"
    page = '- textbox "Email" [ref=e1]: a@b.c\n- textbox "Password" [ref=e2]: x\n- button "Login" [ref=e3]\n'
    res = ("### Ran Playwright code\n```js\nawait page.locator('[data-test=\"login-submit\"]').click();\n```\n"
           "### Result\nSame page. Changes:\n+ generic \"Invalid email or password\" [ref=e9]")
    rec.action("browser_click", {"ref": "e3"}, Snap(u, "Shop", page),
               Snap(u, "Shop", page + "- generic [ref=e9]: Invalid email or password\n"), res)
    s = rec.steps[-1]
    assert s["messages"] == ["Invalid email or password"]
    assert s["code"] == "page.locator('[data-test=\"login-submit\"]').click()"
    assert "code: page.locator" in rec.pack(code=True) and "code:" not in rec.pack()
    res2 = ("### Result\nSame page. Changes:\n+ heading \"Searched for: Pliers\" [ref=e5]\n"
            "+ paragraph \"4 products found\" [ref=e6]\n~ link \"cart\": 1  ->  link \"cart\": 2 [ref=e7]")
    rec.action("browser_click", {"ref": "e3"}, Snap(u, "Shop", page), Snap(u, "Shop", page), res2)
    assert rec.steps[-1]["appeared"] == ["Searched for: Pliers", "4 products found", 'link "cart": 1 -> link "cart": 2']


def test_repeated_failed_sign_in_adds_a_lockout_note():
    rec = Recorder()
    u = "https://shop.test/login"
    page = '- textbox "Email" [ref=e1]: a@b.c\n- textbox "Password" [ref=e2]: x\n- button "Login" [ref=e3]\n'
    err = Snap(u, "Shop", page + '- alert: "Invalid email or password"\n')
    assert rec.action("browser_click", {"ref": "e3"}, Snap(u, "Shop", page), err, "") is None
    note = rec.action("browser_click", {"ref": "e3"}, Snap(u, "Shop", page), err, "")
    assert note and "failed 2 times" in note


def test_followup_hook_gives_the_record_instead_of_the_exploration():
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from lite_locator.evidence import followup_hook
    rec = explored()
    hook = followup_hook(rec)
    call = AIMessage("", tool_calls=[{"name": "browser_click", "args": {"ref": "e3"}, "id": "c1"}])
    msgs = [HumanMessage("Explore the checkout"), call, ToolMessage("Navigated ... big tool output", tool_call_id="c1"),
            AIMessage("DONE")]
    assert len(hook({"messages": msgs})["llm_input_messages"]) == 4          # still exploring: unchanged
    out = hook({"messages": msgs + [HumanMessage("Now write the Playwright test scripts")]})["llm_input_messages"]
    assert len(out) == 2 and "Explore the checkout" in out[0].content and "What was done" in out[0].content
    assert "big tool output" not in out[0].content and out[1].content == "Now write the Playwright test scripts"


def test_followup_hook_input_is_stable_across_writing_calls():
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from lite_locator.evidence import followup_hook
    rec = explored()
    hook = followup_hook(rec, trim=False)
    call = AIMessage("", tool_calls=[{"name": "browser_click", "args": {"ref": "e3"}, "id": "c1"}])
    msgs = [HumanMessage("Explore"), call, ToolMessage("```yaml\n- button \"x\"\n```", tool_call_id="c1"),
            AIMessage("DONE"), HumanMessage("Write the cases")]
    assert hook({"messages": msgs[:4]})["llm_input_messages"] == msgs[:4]        # exploring: append-only, untouched
    first = hook({"messages": msgs})["llm_input_messages"]
    second = hook({"messages": msgs + [AIMessage("part 1"), HumanMessage("continue")]})["llm_input_messages"]
    assert second[:len(first)] == first                                          # same prefix: the cache can hit
