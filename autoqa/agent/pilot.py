"""Bounded information-level loop. act is the only engine-execution branch."""

import json
from dataclasses import asdict

from pydantic import ValidationError

from autoqa.config import combat_enabled
from autoqa.prompts import builder

from .client import InformationLeakError, ModelAPIError, replay_turn, tool_output
from .grounder import GroundingBudgetExceeded
from .guards import Guards
from .memory import Memory
from .navigation import validate_navigation
from .tools import models_for_combat


class Pilot:
    def __init__(self, config, session, client, log, *, briefing_l2=None, grounder=None):
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
        self.briefing_l2, self.grounder = briefing_l2, grounder
        self.locate_calls = 0
        self.combat = combat_enabled(config.scenario)
        self.tool_models = models_for_combat(self.combat)
        # Evidence registry for validating citations, not an additional model memory.
        self.grounding_receipts = {}

    def charge_grounding(self, model, cost_usd, usage, latency_ms):
        self.guards.total_usd += cost_usd
        for key in ("input_tokens", "output_tokens"):
            self.tokens[key] += usage.get(key, 0) or 0
        for key, parent in (
            ("cached_tokens", "input_tokens_details"),
            ("reasoning_tokens", "output_tokens_details"),
        ):
            self.tokens[key] += (usage.get(parent) or {}).get(key, 0) or 0
        self.log(
            "grounding_request", model=model, cost_usd=cost_usd, usage=usage, latency_ms=latency_ms
        )
        if self.guards.budget_exhausted():
            raise GroundingBudgetExceeded("Budget exhausted during grounding")

    def run_episode(self):
        for decision in range(self.config.max_decisions):
            if self.session.episode_finished():
                return "engine_finished"
            if self.guards.budget_exhausted():
                return "budget_exhausted"
            self.decisions = decision + 1
            obs = self.session.observe()
            cycle = self.guards.observe_view(obs.frame_jpeg, obs.decision_idx)
            if cycle:
                self.log("visual_cycle_warning", decision_idx=decision, **cycle)
            items = builder.build(
                self.config.level,
                self.memory,
                obs,
                self.guards.warning,
                self.briefing_l2,
                combat=self.combat,
            )
            acted, misses = False, 0
            locate_results = {}  # exact same observed frame/concept only; no cross-frame reuse
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
                if self.combat:
                    self.log(
                        "model_decision",
                        decision_idx=decision,
                        call_idx=call_idx,
                        frame_sha256=obs.frame_sha256,
                        output=[item for item in turn.output if item.get("type") != "reasoning"],
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
                        validated[item["call_id"]] = self.tool_models[item["name"]].model_validate(
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
                # A model cannot cite a result first produced by this same response.
                # Only receipts returned on earlier tool rounds were visible to it.
                visible_receipts = self.grounding_receipts.copy()
                # Read-only tools use the observed frame before any act in the batch.
                for item in sorted(calls, key=lambda call: call["name"] in {"act", "finish"}):
                    args = validated[item["call_id"]]
                    self.log(
                        "tool_call",
                        decision_idx=decision,
                        call_id=item["call_id"],
                        name=item["name"],
                        arguments=args.model_dump(),
                    )
                    if item["name"] == "locate":
                        self.locate_calls += 1
                        reused = self.config.level > 1 and args.concept in locate_results
                        if reused:
                            out = locate_results[args.concept].copy()
                            self.log("locate_reused", decision_idx=decision, concept=args.concept)
                        elif self.grounder is None:
                            out = {
                                "found": False,
                                "status": "could_not_verify",
                                "reason": "No grounder configured",
                            }
                        else:
                            try:
                                measured, _ = self.grounder.locate(obs.frame_jpeg, args.concept)
                                self.log(
                                    "locate",
                                    decision_idx=decision,
                                    concept=args.concept,
                                    **measured,
                                )
                                out = {key: measured[key] for key in ("found", "matches", "method")}
                            except GroundingBudgetExceeded:
                                return "budget_exhausted"
                            except Exception as error:
                                out = {
                                    "found": False,
                                    "status": "could_not_verify",
                                    "error_type": type(error).__name__,
                                }
                                self.log("grounding_error", decision_idx=decision, **out)
                        receipt = None
                        if self.config.level > 1:
                            receipt = f"d{decision}:{args.concept}"
                            self.grounding_receipts = {
                                ref: value
                                for ref, value in self.grounding_receipts.items()
                                if decision - value["decision"] < 20
                            }
                            self.grounding_receipts[receipt] = dict(
                                decision=decision,
                                concept=args.concept,
                                found=out.get("found", False),
                                status=out.get("status", "completed"),
                                identity_verified=False,
                                frame_sha256=obs.frame_sha256,
                            )
                            out["evidence_ref"] = receipt
                            out["observed_decision"] = decision
                            locate_results[args.concept] = out.copy()
                        self.memory.record_locate(
                            obs.decision_idx, args.concept, out, receipt=receipt
                        )
                    elif item["name"] == "note":
                        nav = None
                        if self.config.level > 1 and "NAV " in args.text:
                            try:
                                nav = validate_navigation(
                                    args.text,
                                    self.config.level,
                                    decision,
                                    visible_receipts,
                                    current=True,
                                )
                            except ValueError as error:
                                results.append(tool_output(item["call_id"], dict(error=str(error))))
                                self.log(
                                    "navigation_rejected", decision_idx=decision, reason=str(error)
                                )
                                continue
                        out = self.memory.note(args.text, args.mode)
                        self.log(
                            "landmark_ledger",
                            decision_idx=decision,
                            source="model_note",
                            text=self.memory.scratchpad,
                        )
                        if nav:
                            self.log(
                                "navigation_update",
                                decision_idx=decision,
                                source="model_note",
                                **nav.model_dump(),
                                grounding_evidence={
                                    ref: self.grounding_receipts[ref] for ref in nav.grounding_refs
                                },
                                planning_verified=False,
                            )
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
                        if self.config.level > 1:
                            try:
                                nav = validate_navigation(
                                    self.memory.scratchpad,
                                    self.config.level,
                                    decision,
                                    visible_receipts,
                                    current=bool(cycle),
                                )
                            except ValueError as error:
                                results.append(tool_output(item["call_id"], dict(error=str(error))))
                                self.log(
                                    "navigation_rejected", decision_idx=decision, reason=str(error)
                                )
                                continue
                            self.log(
                                "navigation_action",
                                decision_idx=decision,
                                source="model_act",
                                navigation_at=nav.at,
                                basis=nav.basis,
                                destination=nav.destination,
                                subgoal=nav.subgoals[0],
                                grounding_refs=nav.grounding_refs,
                                intent=args.intent,
                                planning_verified=False,
                            )
                        feedback = self.session.execute(args)
                        out = asdict(feedback)
                        self.actions += 1
                        acted = True
                        self.guards.idle_decisions = 0
                        self.memory.record(
                            obs.decision_idx,
                            f"act {args.model_dump()}; visible dFrame={feedback.frame_change_pct:.2f}% (not proof of interaction success)",
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
