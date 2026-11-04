"""Offline audit geometry must not turn overlap proxies into aim/identity claims."""

import math

import numpy as np
import pytest

from scripts.audit_m35 import aim_gap, aligned_mask, decision_blocks, enemies


def label(category, box, value=1):
    return dict(name="test", category=category, box=box, value=value)


def test_horizontal_gap_uses_inclusive_enemy_box_and_excludes_self_and_gore():
    assert aim_gap([label("Monster", [319, 10, 2, 5])]) == 0
    assert aim_gap([label("Monster", [321, 10, 1, 5])]) == pytest.approx(
        math.degrees(math.atan(1 / 320))
    )
    assert aim_gap([label("Self", [0, 0, 640, 480])]) is None
    assert enemies([label("Gore", [300, 10, 40, 50])]) == []


def test_mask_iou_accounts_for_union_and_can_select_non_enemy_object():
    buffer = np.array([[1, 1, 2], [0, 0, 2]], dtype=np.uint8)
    mask = np.array([[False, True, True], [False, False, True]])
    items = [label("Monster", [0, 0, 2, 1], 1), label("Health", [2, 0, 1, 2], 2)]
    best = aligned_mask(mask, buffer, items)
    assert best["category"] == "Health"
    assert best["iou"] == pytest.approx(2 / 3)
    assert aligned_mask(mask, buffer, enemies(items))["iou"] == pytest.approx(1 / 4)


def test_background_only_mask_has_no_label_match():
    buffer = np.array([[0, 0], [1, 1]], dtype=np.uint8)
    mask = np.array([[True, True], [False, False]])
    assert aligned_mask(mask, buffer, [label("Monster", [0, 1, 2, 1])]) is None


def test_block_costs_inherit_grounding_decision_and_preserve_global_revisits():
    events = [
        dict(event="model_response", decision_idx=59, cost_usd=0.1),
        dict(event="grounding_request", cost_usd=0.02),
        dict(event="model_response", decision_idx=60, cost_usd=0.03),
        dict(event="grounding_request", cost_usd=0.01),
        dict(event="action_feedback", decision_idx=60),
    ]
    telemetry = [
        dict(event="init", decision_idx=0, cell_x=0, cell_y=0, kills=0, health=100),
        dict(event="primitive", decision_idx=59, cell_x=1, cell_y=0, kills=1, health=90),
        dict(event="primitive", decision_idx=60, cell_x=0, cell_y=0, kills=1, health=90),
        dict(event="primitive", decision_idx=60, cell_x=0, cell_y=0, kills=1, health=90),
    ]
    first, second = decision_blocks(events, telemetry)
    assert first["recorded_usd"] == pytest.approx(0.12)
    assert second["recorded_usd"] == pytest.approx(0.04)
    assert first["new_sampled_cells"] == 2
    assert second["new_sampled_cells"] == 0
    assert second["sampled_cell_reentries"] == 1
    assert second["executed_acts"] == 1
