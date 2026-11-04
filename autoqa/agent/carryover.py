"""Deterministic self-observed feedback; never imports or reads evaluation artifacts."""

import hashlib
import json
from pathlib import Path

HEADER = "## Observations from Your Previous Attempt on This Level"


def extract(directory, *, protocol="self_observed_only_v2"):
    if protocol not in {"self_observed_only_v1", "self_observed_only_v2"}:
        raise ValueError("Unknown carry-over extraction protocol")
    directory = Path(directory)
    # Explicit input allowlist. No directory traversal/globbing of evaluator outputs.
    sources = {
        name: (directory / name).read_bytes()
        for name in ("manifest.json", "events.jsonl", "memory.json", "issues.json")
    }
    manifest = json.loads(sources["manifest.json"])
    if manifest.get("outcome") in (None, "running"):
        raise ValueError("Carry-over requires a finalized prior attempt")
    memory = json.loads(sources["memory.json"])
    events = [json.loads(line) for line in sources["events.jsonl"].splitlines() if line.strip()]
    issues = json.loads(sources["issues.json"])
    warnings = [e for e in events if e.get("event") in {"stuck_warning", "visual_cycle_warning"}]
    own_issues = [i["summary"] for i in issues if i.get("source", "pilot_inline") == "pilot_inline"]
    # Reserve space for warnings/issues so a long scratchpad cannot erase them.
    text = f"Previous attempt ended {manifest['outcome']}; {manifest.get('decisions', 'unknown')} decisions. These are your uncertain observations, not verified facts.\n"
    text += "Own reported concerns: " + ("; ".join(own_issues)[:120] or "none") + "\n"
    text += (
        "Repeat warnings at decisions: "
        + (", ".join(str(e["decision_idx"]) for e in warnings[-6:]) or "none")
        + "\n"
    )
    text += "Own landmark/outcome notes: " + memory.get("landmark_outcome_ledger", "")
    if protocol == "self_observed_only_v2":
        concerns = "; ".join(own_issues)[:100] or "none"
        contexts = []
        for warning in warnings[-3:]:
            step = warning["decision_idx"]
            act = next(
                (
                    e.get("arguments", {})
                    for e in events
                    if e.get("event") == "tool_call"
                    and e.get("name") == "act"
                    and e.get("decision_idx") == step
                ),
                {},
            )
            moves = ",".join(
                f"{move.get('type')}:{move.get('amount')}" for move in act.get("moves", [])
            )
            contexts.append(f"d{step} {moves[:65]} intent={act.get('intent', '')[:65]}")
        text = f"Prior observed outcome: {manifest['outcome']}; decisions: {manifest.get('decisions', 'unknown')}. All notes/identities remain tentative.\n"
        text += "Own concerns: " + concerns + "\n"
        text += "Repeated-view/action warnings: " + ("; ".join(contexts)[:280] or "none") + "\n"
        text += "Own landmark/outcome notes: " + memory.get("landmark_outcome_ledger", "")
    brief = text[:800]
    return dict(
        brief=brief,
        brief_sha256=hashlib.sha256(brief.encode()).hexdigest(),
        source_run_id=manifest["run_id"],
        scenario=manifest["scenario_id"],
        level=manifest["level"],
        seed=manifest["seed"],
        source_sha256={name: hashlib.sha256(data).hexdigest() for name, data in sources.items()},
        protocol=protocol,
    )


def prompt_text(brief):
    if not isinstance(brief, str) or not 1 <= len(brief) <= 800:
        raise ValueError("Carry-over brief must contain 1–800 characters")
    return (
        HEADER
        + "\n"
        + brief
        + "\nTreat old locations and identities as tentative. Avoid repeating actions that triggered warnings; test alternative routes or interactions early. Write fresh current navigation notes from this attempt."
    )
