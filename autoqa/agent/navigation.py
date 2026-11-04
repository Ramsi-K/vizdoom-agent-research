"""Validate model-written navigation annotations in the existing note ledger.

No localization, route search, or action selection is performed here.
"""

import json
import re
from typing import Annotated, Literal

from pydantic import Field

from autoqa.config import StrictModel

Short = Annotated[str, Field(min_length=1, max_length=100)]


class NavigationIntent(StrictModel):
    at: int = Field(ge=0)
    basis: Literal["map", "grounding", "visual"]
    location: Short
    heading: Short
    destination: Short
    subgoals: list[Short] = Field(min_length=1, max_length=3)
    grounding_refs: list[str] = Field(max_length=2)
    uncertainty: Short
    replan: Short
    next_check: Short


def read_navigation(text):
    matches = re.findall(r"(?:^|\n)NAV (\{[^\n]*\})", text)
    return NavigationIntent.model_validate(json.loads(matches[-1])) if matches else None


def validate_navigation(text, level, decision, receipts, *, current=False):
    nav = read_navigation(text)
    if nav is None:
        raise ValueError("Add a single-line NAV JSON annotation to your existing note ledger")
    if nav.at > decision or decision - nav.at > 5 or (current and nav.at != decision):
        raise ValueError("Update NAV at this decision; plans expire after five decisions")
    if level == 3 and nav.basis != "map":
        raise ValueError("L3 strategy must cite the supplied map; location may be uncertain")
    if level == 2 and (nav.basis == "map" or not nav.grounding_refs):
        raise ValueError("L2 must cite an observed locate receipt; failed lookups remain uncertain")
    for ref in nav.grounding_refs:
        receipt = receipts.get(ref)
        if receipt is None or not 0 <= decision - receipt["decision"] < 20:
            raise ValueError("Grounding references must cite actual recent locate receipts")
    if nav.basis == "grounding" and not nav.grounding_refs:
        raise ValueError("A grounding-based intention needs an observed locate reference")
    return nav
