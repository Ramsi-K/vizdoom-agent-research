import ast
import json
from pathlib import Path

from autoqa.agent.client import ModelTurn
from autoqa.agent.guards import STUCK_WARNING
from autoqa.agent.pilot import Pilot
from autoqa.config import RunConfig
from autoqa.doom.types import ActionFeedback


def turn(name="act", arguments=None, call_id="call"):
    args = arguments or {
        "moves": [{"type": "forward", "amount": 10}],
        "run": False,
        "intent": "test movement",
    }
    return ModelTurn(
        [
            {"type": "reasoning", "encrypted_content": "opaque", "summary": []},
            {
                "type": "function_call",
                "name": name,
                "call_id": call_id,
                "arguments": json.dumps(args),
            },
        ],
        {"input_tokens": 10, "output_tokens": 10},
        1,
        0.00001,
        "completed",
    )


class FakeSession:
    level = 1

    def __init__(self, obs):
        self.obs, self.actions = obs, []

    def observe(self):
        return self.obs

    def execute(self, args):
        self.actions.append(args.model_dump())
        return ActionFeedback(10, 20, 0.0, len(args.moves), False)

    def episode_finished(self):
        return False


class FakeClient:
    def __init__(self, turns):
        self.turns, self.requests = iter(turns), []

    def responses(self, items, obs, remaining_seconds):
        self.requests.append(items)
        return next(self.turns)


def test_only_act_branch_can_execute():
    tree = ast.parse(Path("autoqa/agent/pilot.py").read_text())
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "execute"
    ]
    assert len(calls) == 1
    assert any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and any(
            isinstance(value, ast.Constant) and value.value == "act"
            for value in node.test.comparators
        )
        and calls[0] in list(ast.walk(node))
        for node in ast.walk(tree)
    )
    assert "m0" not in Path("autoqa/run.py").read_text()


def test_repeat_guard_only_adds_text_without_altering_actions(observation):
    session = FakeSession(observation)
    client = FakeClient([turn(call_id=f"c{i}") for i in range(4)])
    events = []
    pilot = Pilot(
        RunConfig(max_decisions=4), session, client, lambda event, **kw: events.append((event, kw))
    )
    assert pilot.run_episode() == "max_decisions"
    assert len(session.actions) == 4
    assert all(a == session.actions[0] for a in session.actions)
    assert STUCK_WARNING in str(client.requests[3])
    assert any(event == "stuck_warning" for event, _ in events)


def test_rejected_action_never_reaches_engine(observation):
    invalid = {"moves": [{"type": "forward", "amount": 99}], "run": False, "intent": "too long"}
    session = FakeSession(observation)
    client = FakeClient(
        [turn(arguments=invalid), turn("finish", {"status": "blocked", "rationale": "end"}, "end")]
    )
    pilot = Pilot(RunConfig(), session, client, lambda *a, **k: None)
    assert pilot.run_episode() == "blocked"
    assert session.actions == []
    assert "nothing was executed" in str(client.requests[1])


def test_text_only_outputs_abort_without_fallback(observation):
    empty = ModelTurn([], {"input_tokens": 1, "output_tokens": 1}, 0, 0, "completed")
    client = FakeClient([empty] * 5)
    session = FakeSession(observation)
    assert (
        Pilot(RunConfig(), session, client, lambda *a, **k: None).run_episode()
        == "invalid_api_error"
    )
    assert len(client.requests) == 5
    assert session.actions == []


def test_budget_stops_before_executing_returned_action(observation):
    response = turn()
    session = FakeSession(observation)
    pilot = Pilot(
        RunConfig(max_usd=0.000001), session, FakeClient([response]), lambda *a, **k: None
    )
    assert pilot.run_episode() == "budget_exhausted"
    assert session.actions == []


def test_notes_and_inline_issue_are_replayed_then_act_executes(observation):
    issue = dict(
        category="UNCLEAR_OBJECTIVE",
        severity="low",
        summary="Goal unclear",
        evidence_steps=[],
        confidence=0.6,
    )
    client = FakeClient(
        [
            turn("note", dict(text="My observation", mode="append"), "n"),
            turn("report_issue", issue, "i"),
            turn(call_id="a"),
        ]
    )
    session = FakeSession(observation)
    pilot = Pilot(RunConfig(max_decisions=1), session, client, lambda *a, **k: None)
    assert pilot.run_episode() == "max_decisions"
    assert len(session.actions) == 1
    assert "My observation" in pilot.memory.scratchpad
    assert pilot.issues[0]["trace_verification_status"] == "not_run"
    assert any(i.get("encrypted_content") == "opaque" for i in client.requests[1])
    assert any(
        i.get("type") == "function_call_output" and i["call_id"] == "i" for i in client.requests[2]
    )


def test_multiple_act_calls_are_rejected_without_partial_execution(observation):
    double = turn()
    output = double.output + [turn(call_id="second").output[-1]]
    double = ModelTurn(output, double.usage, 0, 0, "completed")
    session = FakeSession(observation)
    client = FakeClient([double, turn("finish", dict(status="gave_up", rationale="stop"), "f")])
    assert Pilot(RunConfig(), session, client, lambda *a, **k: None).run_episode() == "gave_up"
    assert session.actions == []


def test_locate_returns_evidence_without_advancing_game(observation):
    session = FakeSession(observation)
    client = FakeClient([turn("locate", dict(concept="green armor"), "l"), turn(call_id="a")])

    class FakeGrounder:
        def locate(self, frame, concept):
            assert session.actions == []
            assert frame == observation.frame_jpeg
            return dict(found=False, matches=[], method="sam_text"), []

    pilot = Pilot(
        RunConfig(max_decisions=1), session, client, lambda *a, **k: None, grounder=FakeGrounder()
    )
    assert pilot.run_episode() == "max_decisions"
    assert len(session.actions) == 1
    assert '"found": false' in str(client.requests[1])


def test_grounding_budget_stops_parallel_act_without_rewriting_it(observation):
    session = FakeSession(observation)
    act = turn(call_id="a")
    response = ModelTurn(
        act.output + [turn("locate", dict(concept="vest"), "l").output[-1]],
        act.usage,
        0,
        0,
        "completed",
    )
    client = FakeClient([response])
    pilot = Pilot(RunConfig(max_usd=0.001), session, client, lambda *a, **k: None)

    class ExpensiveGrounder:
        def locate(self, frame, concept):
            pilot.charge_grounding("sam-3.1", 0.0025, {}, 0)

    pilot.grounder = ExpensiveGrounder()
    assert pilot.run_episode() == "budget_exhausted"
    assert session.actions == []
