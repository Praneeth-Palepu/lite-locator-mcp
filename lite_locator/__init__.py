"""lite-locator: shortlist the page elements for a test step, so the LLM gets a few candidates instead of the whole
accessibility tree. See elem_rank.locate for the full API."""
from elem_rank.diff import Snap, outcome
from elem_rank.locate import Hits, Locator, aact, act, aobserve, locator, observe

__all__ = ["Locator", "Hits", "locator", "act", "aact", "observe", "aobserve", "outcome", "Snap"]
__version__ = "0.1.4"
