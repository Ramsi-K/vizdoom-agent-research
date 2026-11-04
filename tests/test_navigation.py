import hashlib
import json

import pytest

from autoqa.agent.navigation import validate_navigation
from autoqa.agent.tools import TOOLS, tools_for_level
from autoqa.prompts.builder import prefix


def nav_text(at=0, basis="map", refs=None):
    return (
        "NAV "
        + json.dumps(
            dict(
                at=at,
                basis=basis,
                location="near S?",
                heading="north?",
                destination="G",
                subgoals=["upper junction", "right branch"],
                grounding_refs=refs or [],
                uncertainty="appearance correspondence unknown",
                replan="initial",
                next_check="a junction should appear",
            )
        )
        + "\nExisting model-written landmark/outcome entry."
    )


def test_l1_prefix_and_tools_are_byte_identical_to_prechange_baseline():
    # Frozen before this L2/L3 change, not generated from the implementation under test.
    baseline = {
        "l1_prefix_sha256": "db51a3e4a27a21c49fc4821e997a554e78301e4aff4bf34bfb5ec50c1779da88",
        "l1_tools_sha256": "9d2159f8bf5b3356c665886d1d56e34d286e26b9de89d4775ea4089473559895",
    }
    assert hashlib.sha256(prefix().encode()).hexdigest() == baseline["l1_prefix_sha256"]
    assert (
        hashlib.sha256(json.dumps(tools_for_level(1), sort_keys=True).encode()).hexdigest()
        == baseline["l1_tools_sha256"]
    )
    assert tools_for_level(1) is TOOLS
    assert "NAV " not in prefix()


def test_map_plan_and_failed_grounding_are_uncertain_model_intentions():
    nav = validate_navigation(nav_text(), 3, 0, {}, current=True)
    assert nav.location == "near S?"
    receipts = {"d0:panel": dict(decision=0, found=False, identity_verified=False)}
    nav = validate_navigation(nav_text(basis="visual", refs=["d0:panel"]), 2, 0, receipts)
    assert nav.basis == "visual"  # a failed lookup does not become a successful detection


@pytest.mark.parametrize(
    "text,level,decision,receipts",
    [
        ("plain ledger without NAV", 3, 0, {}),
        (nav_text(basis="visual"), 3, 0, {}),
        (nav_text(basis="map"), 2, 0, {}),
        (nav_text(basis="grounding", refs=["invented"]), 2, 0, {}),
        (nav_text(at=1), 3, 0, {}),
        (nav_text(), 3, 6, {}),
    ],
)
def test_missing_forged_future_and_stale_navigation_is_rejected(text, level, decision, receipts):
    with pytest.raises(ValueError):
        validate_navigation(text, level, decision, receipts)
