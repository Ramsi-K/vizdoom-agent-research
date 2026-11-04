"""Bounded Level 1 loop. act is the only engine-execution branch."""

import json
from dataclasses import asdict

from pydantic import ValidationError

from autoqa.prompts import builder

from .client import InformationLeakError, ModelAPIError, replay_turn, tool_output
from .guards import Guards
from .memory import Memory
from .tools import MODELS


class Pilot:
    def __init__(self, config, session, client, log):
        self.config, self.session, self.client, self.log = config, session, client, log
        self.memory, self.guards = Memory(), Guards(config)
        self.issues = []
        self.calls = 0
        self.decisions = 0
        self.actions = 0
        self.tokens = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
        }

    def run_episode(self):
        for decision in range(self.config.max_decisions):
            if self.session.episode_finished():
                return "engine_finished"
            if self.guards.budget_exhausted():
                return "budget_exhausted"
            self.decisions = decision + 1
            obs = self.session.observe()
            items = builder.build(1, self.memory, obs, self.guards.warning)
            acted, misses = False, 0
            for call_idx in range(4):
                if self.guards.budget_exhausted():
                    return "budget_exhausted"
                try:
                    turn = self.client.responses(items, obs, self.guards.remaining_seconds())
                except (ModelAPIError, InformationLeakError) as error:
                    if self.guards.budget_exhausted():
                        self.log(
                            "budget_exhausted",
                            decision_idx=decision,
                            reason="wall-clock deadline during API request",
                        )
                        return "budget_exhausted"
                    self.log("invalid_api_error", decision_idx=decision, reason=str(error))
                    return "invalid_api_error"
                self.calls += 1
                self.guards.total_usd += turn.cost_usd
                for key in ("input_tokens", "output_tokens"):
                    self.tokens[key] += turn.usage[key]
                for key, parent in (
                    ("cached_tokens", "input_tokens_details"),
                    ("reasoning_tokens", "output_tokens_details"),
                ):
                    self.tokens[key] += (turn.usage.get(parent) or {}).get(key, 0) or 0
                self.log(
                    "model_response",
                    decision_idx=decision,
                    call_idx=call_idx,
                    output_types=[i["type"] for i in turn.output],
                    usage=turn.usage,
                    latency_ms=turn.latency_ms,
                    cost_usd=turn.cost_usd,
                    status=turn.status,
                )
                calls = [i for i in turn.output if i.get("type") == "function_call"]
                if self.guards.budget_exhausted():
                    self.log("budget_exhausted", decision_idx=decision, unexecuted_calls=len(calls))
                    return "budget_exhausted"
                if not calls or turn.status != "completed":
                    self.guards.empty_outputs += 1
                    misses += 1
                    self.log(
                        "no_action",
                        decision_idx=decision,
                        reason="empty, refused, text-only or incomplete output",
                    )
                    if self.guards.empty_outputs >= 5:
                        return "invalid_api_error"
                    if misses >= 2:
                        break
                    items = (
                        items
                        + turn.output
                        + [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": "Return a tool call to continue or finish.",
                                    }
                                ],
                            }
                        ]
                    )
                    continue
                results = []
                self.guards.empty_outputs = 0
                validated = {}
                try:
                    if sum(i["name"] in {"act", "finish"} for i in calls) > 1:
                        raise ValueError("At most one act or finish per response")
                    for item in calls:
                        validated[item["call_id"]] = MODELS[item["name"]].model_validate(
                            json.loads(item["arguments"])
                        )
                except (ValueError, KeyError, ValidationError):
                    results = [
                        tool_output(
                            i["call_id"],
                            {
                                "error": "Invalid tool call. Check schema, primitive limits, and 70-tic total; nothing was executed."
                            },
                        )
                        for i in calls
                    ]
                    self.log(
                        "rejected_tool_calls",
                        decision_idx=decision,
                        calls=[
                            dict(name=i["name"], call_id=i["call_id"], arguments=i["arguments"])
                            for i in calls
                        ],
                    )
                    items = replay_turn(items, turn.output, results)
                    continue
                stop = None
                for item in calls:
                    args = validated[item["call_id"]]
                    self.log(
                        "tool_call",
                        decision_idx=decision,
                        call_id=item["call_id"],
                        name=item["name"],
                        arguments=args.model_dump(),
                    )
                    if item["name"] == "note":
                        out = self.memory.note(args.text, args.mode)
                    elif item["name"] == "report_issue":
                        data = args.model_dump()
                        steps = data["evidence_steps"] or [obs.decision_idx]
                        if any(s < 0 or s > obs.decision_idx for s in steps):
                            out = {"error": "Evidence steps must reference already observed frames"}
                        else:
                            self.issues.append(
                                dict(
                                    **{**data, "evidence_steps": steps},
                                    source="pilot_inline",
                                    trace_verification_status="not_run",
                                    causal_hypothesis=None,
                                )
                            )
                            out = {
                                "issue_index": len(self.issues) - 1,
                                "trace_verification_status": "not_run",
                            }
                    elif item["name"] == "finish":
                        stop = args.status
                        out = {"stopped": True}
                    elif item["name"] == "act":
                        feedback = self.session.execute(args)
                        out = asdict(feedback)
                        self.actions += 1
                        acted = True
                        self.guards.idle_decisions = 0
                        self.memory.history.append(
                            f"Decision {obs.decision_idx}: {args.model_dump()}; dFrame={feedback.frame_change_pct:.2f}%"
                        )
                        warning = self.guards.observe(args, feedback)
                        if warning:
                            self.log("stuck_warning", decision_idx=decision, text=warning)
                        print(
                            f"Decision {decision}: {len(args.moves)} primitives, tic {feedback.tic_end}, USD {self.guards.total_usd:.5f}",
                            flush=True,
                        )
                    else:
                        raise RuntimeError("Validated tool missing handler")
                    results.append(tool_output(item["call_id"], out))
                self.log("tool_results", decision_idx=decision, results=results)
                if stop:
                    return stop
                if acted:
                    break
                items = replay_turn(items, turn.output, results)
            if not acted:
                self.guards.idle_decisions += 1
                if self.guards.idle_decisions >= 5:
                    return "invalid_api_error"
        return "engine_finished" if self.session.episode_finished() else "max_decisions"
