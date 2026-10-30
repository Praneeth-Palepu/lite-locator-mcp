"""What to rank for a test step: the element it acts on, not the data it types.

"Search for pliers" or "Type pliers in the search box" mention the value ("pliers") more strongly than the element
(the search box), and the value often also appears on the page as a category, a product or a hint text, which then
outranks the real field. For data-entry steps the ranking query is the target element; every other step is ranked as
written.
"""
import re

_T = r"(?:the\s+)?(?P<target>.+?)"
_V = r"[\"'“]?(?P<value>.+?)[\"'”]?"
PATTERNS = [
    # type / enter / input / write / fill in VALUE in|into|on TARGET
    rf"^(?:type|enter|input|write|key in|fill in|put)\s+{_V}\s+(?:in|into|on|inside)\s+{_T}$",
    # fill TARGET with VALUE / set TARGET to VALUE / change TARGET to VALUE / update TARGET to VALUE
    rf"^(?:fill(?: in| out)?|populate)\s+{_T}\s+with\s+{_V}$",
    rf"^(?:set|change|update)\s+{_T}\s+(?:to|as|=)\s+{_V}$",
    # select / choose / pick VALUE from|in TARGET
    rf"^(?:select|choose|pick)\s+{_V}\s+(?:from|in|on)\s+{_T}$",
]
SEARCH = re.compile(rf"^search\s+(?:for\s+)?{_V}(?:\s+(?:in|on|using|with)\s+{_T})?$", re.I)


def target_of(step):
    """The ranking query for a step: its target element for data-entry steps, else the step unchanged.

    >>> target_of("Type pliers in the search box")
    'search box'
    >>> target_of("Search for pliers")
    'search box'
    >>> target_of("Set the quantity to 2")
    'quantity'
    >>> target_of("Click the login button")
    'Click the login button'
    """
    s = re.sub(r"\s+", " ", step.strip().rstrip("."))
    m = SEARCH.match(s)
    if m:  # "search X in the header search bar" keeps its target; "search X in the catalog" means the search box
        t = (m["target"] or "").strip()
        return t if re.search(r"search|box|field|bar|input", t, re.I) else "search box"
    for p in PATTERNS:
        m = re.match(p, s, re.I)
        if m and m["target"].strip():
            return m["target"].strip()
    return step


REF = re.compile(r"ref=(e\d+)|\[(e\d+)\]|^(e\d+)\b")


def clean_ref(value):
    """A candidate line pasted as a ref ('checkbox "Pliers" [ref=e225]') -> 'e225'; anything else unchanged."""
    if isinstance(value, str) and "ref=" in value:
        m = REF.search(value)
        if m:
            return next(g for g in m.groups() if g)
    return value


def clean_args(name, args):
    """Small, safe repairs of LLM tool arguments: a whole candidate line as ref/target, or a file name for
    browser_snapshot (which would hide the tree from the LLM)."""
    args = dict(args)
    for k in ("ref", "target", "startRef", "endRef"):
        if k in args:
            args[k] = clean_ref(args[k])
    if name.endswith("browser_snapshot"):
        args.pop("filename", None)
    return args


ORDINALS = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3, "4th": 3, "fifth": 4,
            "5th": 4, "last": -1}
ORDINAL = re.compile(r"\b(?:open|click|select|choose|view|go to|tap|pick|navigate to)\b.*?\b(" + "|".join(ORDINALS) +
                     r")\b(?!\s+name)", re.I)


def ordinal_of(step):
    """Position for click-style steps that name one: "Open the first product" -> 0, "click the last result" -> -1.
    Field names like "first name" are not positions."""
    m = ORDINAL.search(step)
    return ORDINALS[m[1].lower()] if m else None


def pick_ordinal(groups, pos):
    """The element at `pos` in the page's main repeated list: the largest group of at least 3 links with
    distinct names (product cards, search results). groups: {key: [element index, ...] in page order}."""
    best = max((g for g in groups.values() if len(g) >= 3), key=len, default=None)
    if not best or not -len(best) <= pos < len(best):
        return None
    return best[pos]
