from types import SimpleNamespace

import pytest
from openai import APIStatusError

from autoqa.agent.client import MetaClient, ModelAPIError, replay_turn, tool_output
from autoqa.agent.memory import Memory
from autoqa.config import RunConfig, usage_cost
from autoqa.prompts.builder import build


def test_sdk_payload_and_encrypted_replay(observation):
    calls = []
    output = [
        {"type": "reasoning", "encrypted_content": "opaque", "summary": []},
        {"type": "message", "role": "assistant", "phase": "commentary", "content": []},
        {"type": "function_call", "name": "note", "call_id": "abc", "arguments": "{}"},
    ]
    response = SimpleNamespace(
        status="completed",
        usage=SimpleNamespace(model_dump=lambda: {"input_tokens": 10, "output_tokens": 20}),
        output=[SimpleNamespace(model_dump=lambda exclude_none, v=item: v) for item in output],
    )

    def create(**kwargs):
        calls.append(kwargs)
        return response

    transport = SimpleNamespace(responses=SimpleNamespace(create=create))
    client = MetaClient("dummy", RunConfig(), lambda *a, **k: None, transport=transport)
    request = build(1, Memory(), observation)
    first = client.responses(request, observation, 20)
    second = replay_turn(request, first.output, [tool_output("abc", {"saved": True})])
    client.responses(second, observation, 20)
    for payload in calls:
        assert payload["include"] == ["reasoning.encrypted_content"]
        assert payload["store"] is False
        assert payload["tool_choice"] == "auto"
        assert payload["parallel_tool_calls"] is True
        assert "seed" not in payload and "temperature" not in payload
        assert "previous_response_id" not in payload
    assert calls[1]["input"][-4:-1] == output
    assert calls[1]["input"][-1]["call_id"] == "abc"


def test_unmatched_tool_result_is_rejected():
    with pytest.raises(ModelAPIError):
        replay_turn([], [{"type": "function_call", "call_id": "a"}], [tool_output("b", {})])


def test_reasoning_is_already_part_of_output_cost():
    usage = {
        "input_tokens": 1000,
        "input_tokens_details": {"cached_tokens": 500},
        "output_tokens": 200,
        "output_tokens_details": {"reasoning_tokens": 100},
    }
    assert usage_cost("muse-spark-1.3", usage) == pytest.approx(
        (500 * 1.25 + 500 * 0.15 + 200 * 4.25) / 1_000_000
    )


@pytest.mark.parametrize("status,expected_calls", [(400, 1), (401, 1), (429, 5), (503, 5)])
def test_api_retries_are_bounded_sanitized_and_honor_retry_after(
    status, expected_calls, observation
):
    requests, sleeps, logs = [], [], []

    def create(**kwargs):
        requests.append(kwargs)
        raise APIStatusError(
            "do-not-log-secret",
            response=SimpleNamespace(
                status_code=status, headers={"Retry-After": "2"}, request=SimpleNamespace()
            ),
            body=None,
        )

    client = MetaClient(
        "dummy",
        RunConfig(),
        lambda event, **fields: logs.append((event, fields)),
        transport=SimpleNamespace(responses=SimpleNamespace(create=create)),
        sleep=sleeps.append,
    )
    with pytest.raises(ModelAPIError):
        client.responses(build(1, Memory(), observation), observation, 100)
    assert len(requests) == expected_calls
    assert len(sleeps) == expected_calls - 1
    assert all(delay >= 2 for delay in sleeps)
    assert "do-not-log-secret" not in str(logs)
