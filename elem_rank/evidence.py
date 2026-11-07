"""Exploration evidence for writing test cases: what an agent saw and did, in a few hundred tokens.

An exploring agent's conversation holds everything the test-case writer needs (fields, buttons, exact messages,
amounts), but spread over tens of thousands of tokens of tool traffic; a writer that re-reads it pays for all of
them. The Recorder keeps the facts as the agent works (no LLM calls), and pack() writes them out briefly:

    rec = Recorder()
    tools = with_lite_locator(mcp_tools, recorder=rec)     # the agent explores as usual
    ...
    cases = rec.observed_cases()       # test cases that follow directly from what was seen (exact messages)
    prompt = writer_prompt(criteria, rec, cases)            # the writer reads this instead of the whole conversation
    cases += parse_cases(llm.invoke(prompt).content)
    problems = check(cases, criteria, rec)                  # uncovered criteria, expected texts never seen
    print(render(cases, "markdown"))

Everything here is generic: no site-specific rules.
"""
import csv
import io
import json
import re
from urllib.parse import urlsplit

from .diff import MESSAGE, messages
from .snapshot import parse_tree

FIELDS = {"textbox", "searchbox", "combobox", "checkbox", "radio", "spinbutton", "slider", "switch", "listbox"}
CONTROLS = {"button", "link", "tab", "menuitem"}
MONEY = re.compile(r"[$€£₹¥]\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP|INR)\b")
PER_PAGE = {"controls": 25, "amounts": 12, "messages": 10}
VERB = {"browser_click": "Click", "browser_type": "Type", "browser_fill_form": "Fill", "browser_select_option": "Select",
        "browser_hover": "Hover over", "browser_press_key": "Press", "browser_navigate": "Open",
        "browser_navigate_back": "Go back", "browser_navigate_forward": "Go forward", "browser_drag": "Drag",
        "browser_handle_dialog": "Answer the dialog", "browser_file_upload": "Upload a file",
        "browser_wait_for": "Wait", "browser_tab_select": "Switch tab"}
SUBMITS = {"browser_click", "browser_press_key"}
REFLIKE = re.compile(r"^(?:f\d+)?e\d+$")
# buttons that leave a form rather than submit it
LEAVE = re.compile(r"(?i)\b(cancel|back|close|reset|clear|log ?out|sign ?out|continue shopping|remove)\b")


def _page_key(url):
    u = urlsplit(url or "")
    return (u.path or "/") + (f"?{u.query}" if u.query else "")


def _selector(t):
    """A CSS selector rather than a 'role "name"' label."""
    return bool(t) and not t.startswith(tuple(f"{r} " for r in FIELDS | CONTROLS)) and bool(re.search(r"[#.\[\]=>:]", t))


def _short(t, n=80):
    t = " ".join((t or "").split())
    return t if len(t) <= n else t[:n - 1] + "…"


class Recorder:
    """Facts from an exploration: pages (fields, controls, amounts, messages), the actions taken and what each one
    did. Feed it with see(snap) for every page state and action(...) for every action; with_lite_locator(recorder=)
    does both. No LLM calls; a few kB of memory."""

    def __init__(self):
        self.pages = {}          # page key -> facts
        self.steps = []          # one dict per action, in order
        self.texts = set()       # every label / text seen (lower case): for check()
        self.origin = ""
        self.last = None         # the last page state seen (for probe)

    # -- feeding -------------------------------------------------------------------------------------------------
    def _page(self, url, title=""):
        key = _page_key(url)
        if not self.origin and url:
            u = urlsplit(url)
            self.origin = f"{u.scheme}://{u.netloc}"
        p = self.pages.setdefault(key, {"url": url, "title": "", "headings": [], "fields": {}, "controls": [],
                                        "amounts": [], "messages": [], "order": len(self.pages)})
        p["title"] = p["title"] or (title or "")
        return p

    def see(self, snap):
        """A page state (diff.Snap): its fields, controls, headings, amounts and messages."""
        if snap is None or not snap.snapshot or not (snap.url or "").startswith("http"):
            return
        self.last = snap
        p = self._page(snap.url, snap.title)
        root = parse_tree(snap.snapshot)
        for n in root.walk():
            lab = n.label()
            if lab:
                self.texts.add(" ".join(lab.split()).lower())
            if n.role == "heading" and lab and lab not in p["headings"] and len(p["headings"]) < 8:
                p["headings"].append(_short(lab))
            elif n.role in FIELDS and (n.name or n.attrs.get("placeholder")):
                f = p["fields"].setdefault(_short(n.name, 60), {"role": n.role, "options": []})
                if n.role in ("combobox", "listbox"):
                    for o in n.walk():
                        if o.role == "option" and o.label() and o.label() not in f["options"]:
                            f["options"].append(_short(o.label(), 40))
            elif n.role in CONTROLS and lab:
                c = _short(lab, 50)
                if c not in p["controls"] and len(p["controls"]) < PER_PAGE["controls"]:
                    p["controls"].append(c)
            if n.role not in FIELDS and lab and MONEY.search(lab) and len(p["amounts"]) < PER_PAGE["amounts"]:
                a = _short(lab, 70)
                if a not in p["amounts"]:
                    p["amounts"].append(a)
        for m in messages(snap.snapshot):
            self.texts.add(" ".join(m.split()).lower())
            if m not in p["messages"] and len(p["messages"]) < PER_PAGE["messages"]:
                p["messages"].append(m)

    def action(self, tool, args, pre, after, result=""):
        """One action: the tool (Playwright MCP name), its arguments, the page before and after (diff.Snap or None)
        and the text the agent got back. Records what was done, to what, and what it caused."""
        name = tool.split("__")[-1]
        args = args or {}
        self.see(pre)
        self.see(after)
        before_msgs = set(messages(pre.snapshot)) if pre else set()
        new = [m for m in (messages(after.snapshot) if after else []) if m not in before_msgs]
        new += [ln.split(": ", 1)[1] for ln in (result or "").splitlines() if ln.startswith("Shown briefly (gone now): ")]
        valid = re.findall(r'^- "([^"]*)": (.+)$', (result or "").split("built-in form validation", 1)[1], re.M) \
            if "built-in form validation" in (result or "") else []
        for _, m in valid:
            self.texts.add(m.lower())
        # text that appeared on the same page (results of a search, a count, a status line): the outcome on an SPA
        # whose URL does not change and which shows no alert
        changes = (result or "").split("Same page. Changes:", 1)[1] if "Same page. Changes:" in (result or "") else ""
        appeared = []
        found = re.findall(r'^\+ (?:heading|paragraph|status|text|cell|listitem|generic|link) "?([^"\n]{3,120})', changes, re.M)
        # changed values too, e.g. a cart badge: ~ link "cart": 1  ->  link "cart": 2
        found += [f"{a.strip()} -> {b.strip()}" for a, b in re.findall(r'^~ (.{3,80}?)\s+->\s+(.{1,80}?)(?:\s*\[ref=[^\]]*\])?$',
                                                                       changes, re.M) if not a.startswith("textbox")]
        for text in found:
            text = re.sub(r"\s*\[ref=[^\]]*\]", "", text).strip().rstrip('"').strip()
            if text and text not in new and text not in appeared and len(appeared) < 6:
                appeared.append(text)
        for t in appeared:
            self.texts.add(t.lower())
        target = self._target(args, pre)
        step = {"tool": name, "page": _page_key(pre.url if pre else (after.url if after else "")),
                "target": target, "value": self._value(name, args),
                "to": _page_key(after.url) if after and pre and _page_key(after.url) != _page_key(pre.url) else "",
                "messages": new, "validation": [f'"{f}": {m}' for f, m in valid], "appeared": appeared,
                "no_change": "No change detected" in (result or "") and not new and not valid,
                "error": bool(re.search(r"(?m)^(?:### Error|Error\b)", result or ""))}
        if name in SUBMITS and pre is not None:      # the form as it was when it was submitted
            step["filled"], step["empty"] = self._form(pre)
        if name == "browser_navigate":
            step["to"] = _page_key(args.get("url", ""))
            step["url"] = args.get("url", "")
        self.steps.append(step)

    @staticmethod
    def _target(args, pre):
        ref = args.get("ref") or args.get("target") or ""
        if pre is not None and ref:
            for n in parse_tree(pre.snapshot).walk():
                if n.ref == ref:
                    return f'{n.role} "{_short(n.label(), 50)}"' if n.label() else n.role
        if args.get("element"):
            return _short(args["element"], 50)
        return ref if ref and not REFLIKE.match(ref) else ""   # a CSS selector (newer Playwright MCP takes those)

    @staticmethod
    def _value(name, args):
        if name == "browser_fill_form":
            return "; ".join(f'{f.get("name")} = {f.get("value")}' for f in args.get("fields", []) if isinstance(f, dict))
        v = args.get("text", args.get("key", args.get("values", args.get("value", ""))))
        return ", ".join(map(str, v)) if isinstance(v, list) else str(v or "")

    @staticmethod
    def _form(snap):
        filled, empty = [], []
        for n in parse_tree(snap.snapshot).walk():
            if n.role in ("textbox", "searchbox", "spinbutton") and n.name:
                (filled if n.text else empty).append(_short(n.name, 40))
        return filled, empty

    # -- output --------------------------------------------------------------------------------------------------
    def pack(self, max_steps=80):
        """The evidence as compact text for a test-case writer: pages, the actions in order with what they caused,
        and every exact message with the action that caused it."""
        out = [f"Site: {self.origin}", "", "Pages (fields, controls, amounts, messages seen):"]
        for key, p in sorted(self.pages.items(), key=lambda kv: kv[1]["order"]):
            heads = f"  headings: {' | '.join(p['headings'])}" if p["headings"] else ""
            out.append(f"- {key}  \"{_short(p['title'], 50)}\"" + heads)
            if p["fields"]:
                out.append("  fields: " + ", ".join(
                    f"{k} ({v['role']}{': ' + ' / '.join(v['options'][:8]) if v['options'] else ''})"
                    for k, v in p["fields"].items()))
            if p["controls"]:
                out.append("  controls: " + ", ".join(p["controls"]))
            if p["amounts"]:
                out.append("  amounts: " + " | ".join(p["amounts"]))
            if p["messages"]:
                out.append("  messages: " + " | ".join(f'"{m}"' for m in p["messages"]))
        out += ["", "What was done, in order (→ what it caused):"]
        steps = self.steps[-max_steps:]
        for i, s in enumerate(steps, 1):
            out.append(f"{i}. {self.step_text(s)}{self._effect(s)}")
        return "\n".join(out)

    @staticmethod
    def step_text(s):
        verb = VERB.get(s["tool"], s["tool"].replace("browser_", "").replace("_", " "))
        if s["tool"] == "browser_navigate":
            return f"Open {s.get('url') or s['to']}"
        if s["tool"] in ("browser_type", "browser_select_option") and s["value"]:
            return f'{verb} "{s["value"]}" in {s["target"]}'
        if s["tool"] == "browser_press_key":
            return f'{verb} {s["value"]}' + (f" in {s['target']}" if s["target"] else "")
        if s["tool"] == "browser_fill_form":
            return f"Fill the form: {s['value']}"
        return f"{verb} {s['target']}".strip()

    @staticmethod
    def _effect(s):
        bits = []
        if s.get("empty") and (s["messages"] or s["validation"]):
            bits.append("empty: " + ", ".join(s["empty"]))
        if s["to"] and s["tool"] != "browser_navigate":
            bits.append(f"went to {s['to']}")
        bits += [f'shows "{m}"' for m in s["messages"]]
        bits += [f"browser validation {v}" for v in s["validation"]]
        bits += [f'"{t}" appeared' for t in s.get("appeared", [])]
        if s["no_change"]:
            bits.append("no visible change")
        if s["error"]:
            bits.append("the action failed")
        return ("  → " + "; ".join(bits)) if bits else ""

    def observed_cases(self):
        """Test cases that follow directly from the exploration: every action that made the page show a message
        (an error, a validation message, a confirmation) becomes a case that repeats the path to it and expects that
        exact message. Written by the library, no LLM tokens; the writer is told not to repeat them."""
        cases, seen = [], set()
        for i, s in enumerate(self.steps):
            msgs = s["messages"] + [v.split(": ", 1)[1] for v in s["validation"]]
            if not msgs or s["error"]:
                continue
            key = (s["page"], s["target"], tuple(s.get("empty", ())), tuple(msgs))
            if key in seen:
                continue
            seen.add(key)
            path = self._path_to(i)
            empty = s.get("empty", [])
            neg = any(MESSAGE.search(m) and re.search(r"(?i)error|invalid|required|must|fail|incorrect|wrong|not |"
                                                      r"cannot|can't|unable|denied|locked|sorry", m) for m in msgs)
            steps = [self.step_text(x) for x in path]
            if empty:
                steps.append("Leave " + ", ".join(empty) + " empty")
            steps.append(self.step_text(s))
            what = (" with " + ", ".join(empty) + " empty") if empty else ""
            cases.append({"title": _short(f"{self.step_text(s)}{what} shows \"{msgs[0]}\"", 120),
                          "type": "negative" if neg else "positive", "steps": steps,
                          "expected": [f'"{m}" is shown' for m in msgs], "source": "observed"})
        return cases

    def forms(self):
        """The forms the exploration submitted successfully, per (page, button): the page URL, its fields, the values
        that went through and the fields never tried empty. Buttons that only leave the form (Cancel, Back, ...) and
        actions that failed do not count."""
        tried, found = {}, {}      # per (page, button): fields left empty in some try / the form
        values = {}                # page -> {field: last value typed}
        for s in self.steps:
            if s["tool"] in ("browser_type", "browser_fill_form") and not s["error"]:
                vals = values.setdefault(s["page"], {})
                if s["tool"] == "browser_type":
                    m = re.search(r'"(.+)"$', s["target"])
                    if m:
                        vals[m.group(1)] = s["value"]
                else:
                    for part in s["value"].split("; "):
                        k, _, v = part.partition(" = ")
                        vals[k] = v
            if s["tool"] not in SUBMITS or "filled" not in s or s["error"] or LEAVE.search(s["target"]):
                continue
            key = (s["page"], s["target"])
            fields = s["filled"] + s["empty"]
            if s["filled"] and (s["to"] or key in found):   # a submit that went through with fields filled
                found.setdefault(key, {"page": s["page"], "url": self.pages.get(s["page"], {}).get("url", ""),
                                       "button": s["target"], "fields": fields,
                                       "key": s["value"] if s["tool"] == "browser_press_key" else "",
                                       "values": {f: values.get(s["page"], {}).get(f, "") for f in fields}})
            # a field counts as tried when it was the only one left empty, or the message names it (a form with
            # several empty fields usually reports only the first one)
            said = " ".join(s["messages"] + s["validation"]).lower()
            tried.setdefault(key, set()).update(f for f in s["empty"] if len(s["empty"]) == 1 or f.lower() in said)
        out = []
        for key, f in found.items():
            f["untried"] = [x for x in f["fields"] if x not in tried.get(key, set())]
            out.append(f)
        return out

    def gaps(self):
        """Negative paths the exploration has not tried yet: forms submitted only with every field filled in. probe()
        tries them without an LLM; or send these lines back to the agent."""
        out = []
        for f in self.forms():
            if f["untried"]:
                act = f"press {f['key']}" if f["key"] else ("click " + f["button"]) if f["button"] else "submit the form"
                out.append(f"On {f['page']}: {act} with each of these left empty, one at a time (the others filled): "
                           f"{', '.join(f['untried'])}; note the exact message.")
        return out

    async def probe(self, call, max_tries=12):
        """Try the untried negative paths of forms() in the browser, without an LLM: for each field, open the form's
        page, fill the others with the values that went through, leave this one empty and submit. call(tool, args)
        must run a browser tool through with_lite_locator(recorder=self) (lite_locator.evidence.probe does this), so
        each try is recorded with the exact message it caused. Returns one line per try."""
        log = []
        for f in self.forms():
            for field in f["untried"]:
                if len(log) >= max_tries or not f["url"]:
                    return log
                await call("browser_navigate", {"url": f["url"]})
                await call("browser_snapshot", {})
                refs = self._refs()
                submit_ok = f["key"] or f["button"] in refs or _selector(f["button"])
                missing = [x for x in f["fields"] if x not in refs] + \
                    ([] if submit_ok else [f["button"] or "the submit button"])
                if missing:
                    log.append(f"{f['page']} {field}: skipped, not on the page after opening it ({', '.join(missing)})")
                    continue
                for x in f["fields"]:
                    text = "" if x == field else (f["values"].get(x) or "test")
                    await call("browser_type", {"element": x, "ref": refs[x], "text": text})
                if f["key"]:      # submitted with a key (Enter) in the last field typed into
                    await call("browser_press_key", {"key": f["key"]})
                else:
                    await call("browser_click", {"element": f["button"], "ref": refs.get(f["button"], f["button"])})
                s = self.steps[-1]
                said = s["messages"] + [v.split(": ", 1)[1] for v in s["validation"]]
                log.append(f"{f['page']} {field} empty: " + (" | ".join(said) if said else
                                                              ("went to " + s["to"]) if s["to"] else "no message"))
        return log

    def _refs(self):
        """label -> ref on the last page seen: fields by name, controls as 'role "label"' (as targets are written)."""
        out = {}
        if self.last is None:
            return out
        for n in parse_tree(self.last.snapshot).walk():
            if not n.ref:
                continue
            if n.role in FIELDS and n.name:
                out.setdefault(_short(n.name, 40), n.ref)
                out.setdefault(_short(n.name, 60), n.ref)
            if n.label():
                out.setdefault(f'{n.role} "{_short(n.label(), 50)}"', n.ref)
        return out

    def _path_to(self, i):
        """The actions that led to step i: from the last Open before it, without actions that failed, changed nothing
        or showed an error, and without earlier values typed into a field that was typed into again."""
        start = max([j for j in range(i) if self.steps[j]["tool"] == "browser_navigate"], default=0)
        keep = []
        for s in self.steps[start:i]:
            if s["error"] or s["no_change"] or s["validation"] or (s["messages"] and not s["to"]):
                continue
            if s["tool"] in ("browser_type", "browser_select_option"):
                same = lambda k: k["tool"] == s["tool"] and k["target"] == s["target"] and k["page"] == s["page"]  # noqa: B023
                keep = [k for k in keep if not same(k)]
            keep.append(s)
        return keep


# -- writing ---------------------------------------------------------------------------------------------------------
WRITE = """You are a senior QA engineer. Write manual test cases for the acceptance criteria below.
Use only facts from the exploration evidence: exact field names, button labels, messages and amounts as written
there. Do not invent messages or values that the evidence does not show; if a criterion needs one that was not
observed, write the case anyway and put "(not observed)" after that expected result.
Cover every criterion, with positive and negative cases. One expected result per check.
{existing}
Reply with a JSON list only, no prose:
[{{"ac": [1], "title": "...", "type": "positive|negative|edge", "steps": ["..."], "expected": ["..."]}}]

Acceptance criteria:
{criteria}

Exploration evidence:
{evidence}"""


def writer_prompt(criteria, recorder, existing=()):
    """The prompt for the test-case writer: criteria + the recorder's pack (+ the titles of cases already written,
    e.g. observed_cases(), so they are not written again)."""
    crit = criteria if isinstance(criteria, str) else "\n".join(f"{i}. {c}" for i, c in enumerate(criteria, 1))
    ex = ""
    if existing:
        ex = ("These cases are already written (from what was observed); do not repeat them, write the others. Start\n"
              'the list with one {"observed": "O1", "ac": [...]} item per case below, naming the criteria it covers\n'
              "([] if none):\n" + "\n".join(f"O{i}. {c['title']}" for i, c in enumerate(existing, 1)))
    return WRITE.format(existing=ex, criteria=crit, evidence=recorder.pack())


def merge_cases(observed, written):
    """observed_cases() + the writer's cases: the writer's {"observed": "O1", "ac": [...]} items set the criteria of
    the observed cases (so check() counts them) and are dropped."""
    out = [dict(c) for c in observed]
    rest = []
    for c in written:
        m = re.fullmatch(r"O(\d+)", str(c.get("observed", "")).strip())
        if m and "steps" not in c:
            i = int(m.group(1)) - 1
            if 0 <= i < len(out):
                out[i]["ac"] = [a for a in c.get("ac") or [] if isinstance(a, int)]
            continue
        rest.append(c)
    return out + rest


def parse_cases(text):
    """Test cases from the writer's reply: the first JSON list in it (code fences and prose around it are ignored).
    Takes a string or a list of content blocks; when the reply was cut off mid-list, keeps the complete cases."""
    if isinstance(text, list):     # content blocks, e.g. [{"type": "text", "text": ...}, ...]
        text = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in text)
    text = text if isinstance(text, str) else str(text)
    dec = json.JSONDecoder()
    start = text.find("[")
    first = start
    while start != -1:
        try:
            val, _ = dec.raw_decode(text[start:])
            if isinstance(val, list) and any(isinstance(c, dict) for c in val):
                return [c for c in val if isinstance(c, dict)]
        except ValueError:
            pass
        start = text.find("[", start + 1)
    out, i = [], text.find("{", first) if first != -1 else -1     # cut off: the complete objects of the first list
    while i != -1:
        try:
            val, end = dec.raw_decode(text[i:])
        except ValueError:
            break
        if isinstance(val, dict):
            out.append(val)
        i = text.find("{", i + end)
    return out


def check(cases, criteria, recorder):
    """Problems a reviewer would flag, without an LLM: criteria no case covers, and quoted expected texts that never
    appeared during the exploration (likely invented; "(not observed)" marks the ones the writer already flagged)."""
    n = len(criteria) if not isinstance(criteria, str) else len(re.findall(r"(?m)^\s*\d+[.)]", criteria))
    covered = {a for c in cases for a in (c.get("ac") or []) if isinstance(a, int)}
    seen = set(recorder.texts)     # + the pages visited and the values typed: quoting those is not inventing
    for key, p in recorder.pages.items():
        seen |= {key.lower(), (recorder.origin + key).lower(), (p["url"] or "").lower().rstrip("/")}
    seen |= {s["value"].lower() for s in recorder.steps if s["value"]}
    seen |= {(s.get("url") or "").lower() for s in recorder.steps}
    unverified = []
    for c in cases:
        for e in c.get("expected") or []:
            if "(not observed)" in str(e):
                continue
            for q in re.findall(r'"([^"]{3,})"|(?<!\w)\'((?:[^\']|(?<=\w)\'(?=\w)){3,}?)\'(?!\w)', str(e)):
                q = q[0] or q[1]
                ql = " ".join(q.split()).lower()
                if not any(ql in t for t in seen):
                    unverified.append((c.get("title", ""), q))
    return {"uncovered": sorted(set(range(1, n + 1)) - covered) if n else [], "unverified": unverified}


def render(cases, fmt="markdown"):
    """Test cases as a markdown table (default), CSV, or JSON."""
    rows = [{"id": f"TC{i:02d}", "ac": ", ".join(map(str, c.get("ac") or [])), "type": c.get("type", ""),
             "title": c.get("title", ""), "steps": c.get("steps") or [], "expected": c.get("expected") or []}
            for i, c in enumerate(cases, 1)]
    if fmt == "json":
        return json.dumps(rows, indent=1, ensure_ascii=False)
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["ID", "AC", "Type", "Title", "Steps", "Expected"])
        for r in rows:
            w.writerow([r["id"], r["ac"], r["type"], r["title"], "\n".join(r["steps"]), "\n".join(r["expected"])])
        return buf.getvalue()
    cell = lambda xs: "<br>".join(f"{i}. {x}" for i, x in enumerate(xs, 1)).replace("|", "\\|")
    out = ["| ID | AC | Type | Title | Steps | Expected |", "|---|---|---|---|---|---|"]
    out += [f"| {r['id']} | {r['ac']} | {r['type']} | {r['title'].replace('|', '/')} | {cell(r['steps'])} | "
            f"{cell(r['expected'])} |" for r in rows]
    return "\n".join(out)
