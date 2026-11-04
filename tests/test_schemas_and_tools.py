import pytest
from pydantic import ValidationError

from autoqa.agent.tools import TOOLS
from autoqa.config import Act, RunConfig


def test_m2_exposes_five_tools_with_closed_schemas():
    assert {t["name"] for t in TOOLS} == {"act", "note", "report_issue", "finish", "locate"}

    def check(node, depth=0):
        assert depth <= 10
        if isinstance(node, dict):
            assert "$ref" not in node
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for key, value in node.items():
                check(value, depth + (key in {"properties", "items"}))
        elif isinstance(node, list):
            for value in node:
                check(value, depth)

    for tool in TOOLS:
        check(tool["parameters"])


@pytest.mark.parametrize(
    "moves",
    [
        [{"type": "use", "amount": 2}],
        [{"type": "forward", "amount": 36}],
        [{"type": "turn_left", "amount": 181}],
        [{"type": "forward", "amount": 35}] * 3,
    ],
)
def test_primitive_and_total_limits_are_rejected_not_clamped(moves):
    with pytest.raises(ValidationError):
        Act.model_validate(dict(moves=moves, run=False, intent="test"))


@pytest.mark.parametrize("settings", [dict(level=4), dict(level=0), dict(model="non-meta-model")])
def test_run_settings_reject_future_levels_and_other_providers(settings):
    with pytest.raises(ValidationError):
        RunConfig.model_validate(settings)
