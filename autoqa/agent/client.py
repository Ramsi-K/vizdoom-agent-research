"""Meta-only Responses API, stateless output replay and a narrow input gate."""

import json
import random
import re
import time
from dataclasses import dataclass

from openai import APIConnectionError, APIStatusError, OpenAI

from autoqa.config import usage_cost

from .tools import TOOLS


class InformationLeakError(ValueError):
    pass


class ModelAPIError(RuntimeError):
    pass


def sanitize_request(items, observation):
    images = 0

    def inspect(item):
        nonlocal images
        if isinstance(item, list):
            for child in item:
                inspect(child)
        elif isinstance(item, dict):
            if item.get("type") == "input_image":
                images += 1
            if item.get("type") == "input_text" and re.search(
                r"\b(pos_x|pos_y|labels_buffer|object_position|sector)\b", item.get("text", "")
            ):
                raise InformationLeakError("Forbidden telemetry key in input_text")
            for value in item.values():
                if isinstance(value, (list, dict)):
                    inspect(value)

    inspect(items)
    if observation.plan_png is not None or images > 1 + len(observation.prev_frames_jpeg):
        raise InformationLeakError("Unexpected image or map in Level 1 payload")


def tool_output(call_id, output):
    if not isinstance(call_id, str) or not 1 <= len(call_id) <= 64:
        raise ModelAPIError("Invalid function call ID")
    return dict(type="function_call_output", call_id=call_id, output=json.dumps(output))


def replay_turn(items, output, results):
    calls = [item for item in output if item.get("type") == "function_call"]
    if {c["call_id"] for c in calls} != {r["call_id"] for r in results} or len(calls) != len(
        results
    ):
        raise ModelAPIError("Each tool call requires exactly one matching result")
    return items + output + results


@dataclass(frozen=True)
class ModelTurn:
    output: list[dict]
    usage: dict
    latency_ms: float
    cost_usd: float
    status: str


class MetaClient:
    def __init__(self, key, config, log, *, transport=None, sleep=time.sleep):
        self.config, self.log, self.sleep = config, log, sleep
        self.client = transport or OpenAI(
            api_key=key,
            base_url="https://api.meta.ai/v1",
            max_retries=0,
            timeout=config.request_timeout,
        )

    def responses(self, items, obs, remaining_seconds):
        sanitize_request(items, obs)
        started = time.monotonic()
        for attempt in range(5):
            timeout = min(
                self.config.request_timeout, remaining_seconds - (time.monotonic() - started)
            )
            if timeout <= 0:
                raise ModelAPIError("Wall-clock budget exhausted during API request")
            try:
                response = self.client.responses.create(
                    model=self.config.model,
                    input=items,
                    tools=TOOLS,
                    tool_choice="auto",
                    parallel_tool_calls=True,
                    include=["reasoning.encrypted_content"],
                    store=False,
                    reasoning={"effort": self.config.effort},
                    max_output_tokens=self.config.max_output_tokens,
                    timeout=timeout,
                )
                if response.usage is None:
                    raise ModelAPIError("Missing usage; cannot enforce USD budget")
                usage = response.usage.model_dump()
                return ModelTurn(
                    [item.model_dump(exclude_none=True) for item in response.output],
                    usage,
                    (time.monotonic() - started) * 1000,
                    usage_cost(self.config.model, usage),
                    response.status,
                )
            except (APIStatusError, APIConnectionError) as error:
                status = getattr(error, "status_code", None)
                retry = (
                    isinstance(error, APIConnectionError)
                    or status == 429
                    or (status is not None and status >= 500)
                )
                self.log(
                    "api_error",
                    attempt=attempt + 1,
                    http_status=status,
                    error_type=type(error).__name__,
                    retry=retry,
                )
                if not retry or attempt == 4:
                    raise ModelAPIError(
                        f"Meta API failed: {status or type(error).__name__}"
                    ) from None
                header = (
                    error.response.headers.get("Retry-After")
                    if isinstance(error, APIStatusError)
                    else None
                )
                try:
                    delay = max(0.5 * 2**attempt + random.random() * 0.1, float(header or 0))
                except ValueError:
                    delay = 0.5 * 2**attempt
                if delay >= remaining_seconds - (time.monotonic() - started):
                    raise ModelAPIError("Retry would exceed wall-clock budget") from None
                self.sleep(delay)
        raise ModelAPIError("Meta API retry limit reached")

    def close(self):
        self.client.close()
