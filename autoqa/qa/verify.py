"""Conservative behavioral verification. Confirmation is never design-defect proof."""

import math
from collections import Counter
from pathlib import Path

from autoqa.eval.aggregate import pairing_errors
from autoqa.eval.metrics import number, read_json
from autoqa.io import digest, write_json

from .schemas import IssueRecord
from .topology import branch_observation
from .trace import Trace, distance_to_segment

INTERACTIONS = {"INTERACTION_NO_EFFECT", "BLOCKED_PATH", "SOFTLOCK_SUSPECTED"}


def normalize(trace, finding, index):
    m = trace.manifest
    return IssueRecord(
        issue_id=f"iss_{m['run_id']}_{index:03d}",
        run_id=m["run_id"],
        scenario_id=m["scenario_id"],
        level=m["level"],
        seed=m["seed"],
        source=finding.get("source", "external_candidate"),
        category=finding["category"],
        severity=finding["severity"],
        summary=finding["summary"],
        rationale=finding.get("rationale", ""),
        evidence_steps=sorted(set(finding.get("evidence_steps", []))),
        model_confidence=finding.get("confidence", finding.get("model_confidence", 0)),
        recommended_developer_check=finding.get("recommended_developer_check", ""),
    )


def deduplicate(records):
    result = []
    for record in records:
        overlap = next(
            (
                r
                for r in result
                if r.run_id == record.run_id
                and (r.source != record.source or r.summary == record.summary)
                and r.tic_range
                and record.tic_range
                and (r.category == record.category or {r.category, record.category} <= INTERACTIONS)
                and max(r.tic_range[0], record.tic_range[0])
                <= min(r.tic_range[1], record.tic_range[1]) + 105
            ),
            None,
        )
        if overlap is None:
            result.append(record)
            continue
        overlap.evidence_steps = sorted(set(overlap.evidence_steps + record.evidence_steps))
        overlap.tic_range = [
            min(overlap.tic_range[0], record.tic_range[0]),
            max(overlap.tic_range[1], record.tic_range[1]),
        ]
        overlap.model_confidence = max(overlap.model_confidence, record.model_confidence)
        if overlap.source != record.source:
            overlap.source = "merged"
        overlap.severity = max(
            (overlap.severity, record.severity), key={"low": 0, "medium": 1, "high": 2}.get
        )
        overlap.summary = (
            (overlap.summary + " | " + record.summary)
            if record.summary != overlap.summary
            else overlap.summary
        )
        overlap.rationale = (overlap.rationale + " | " + record.rationale).strip(" |")
        if record.recommended_developer_check:
            overlap.recommended_developer_check = record.recommended_developer_check
    return result


def rule(trace, issue):
    lo, hi, rows, primitives, events = trace.window(issue.tic_range)
    points = [r for r in rows if r.get("event") in {"init", "primitive"}]
    entries = []
    for row in points:
        if not all(number(row.get(k)) for k in ("pos_x", "pos_y")):
            continue
        cell = (math.floor(row["pos_x"] / 128), math.floor(row["pos_y"] / 128))
        if not entries or entries[-1] != cell:
            entries.append(cell)
    measured = [
        r
        for r in points
        if r.get("goal_dist_bfs_status") == "measured_coarse_grid"
        and number(r.get("goal_dist_bfs"))
    ]
    bfs = [r["goal_dist_bfs"] for r in measured]
    warnings = [e for e in events if e.get("event") == "stuck_warning"]
    translations = []
    for event in primitives:
        row = trace.by_step.get((event["decision_idx"], event["primitive_idx"]), {})
        if event.get("requested", {}).get("type") not in {
            "forward",
            "backward",
            "strafe_left",
            "strafe_right",
        }:
            continue
        end_tic = trace.tic(event.get("tic_end"))
        if end_tic is None:
            continue
        elapsed = end_tic - event["tic_start"]
        if elapsed < event.get("requested_tics", elapsed):
            continue  # terminal-truncated movement is not a collision
        if (
            number(row.get("expected_free_displacement"))
            and row["expected_free_displacement"] > 0
            and number(row.get("displacement_units"))
        ):
            translations.append((event, row))
    ratios = [r["displacement_units"] / r["expected_free_displacement"] for _, r in translations]
    metrics = dict(
        window=[lo, hi],
        sampled_cell_entries=len(entries),
        sampled_unique_cells=len(set(entries)),
        translation_samples=len(translations),
        displacement_ratios=ratios,
        stuck_warnings=len(warnings),
    )
    status, note = (
        "unverifiable",
        "Required category-specific evidence is unavailable or inconclusive.",
    )
    category = issue.category
    if category == "INTERACTION_NO_EFFECT":
        uses = [
            e
            for e in primitives
            if e.get("requested", {}).get("type") == "use"
            and e.get("decision_idx") in issue.evidence_steps
        ]
        results = []
        for event in uses:
            tic = trace.tic(event["tic_end"])
            if tic is None:
                continue
            after = [
                r
                for r in trace.rows
                if trace.tic(r.get("tic")) is not None and tic <= trace.tic(r["tic"]) <= tic + 35
            ]
            activated = any(
                r.get("door_or_switch_activated")
                or (number(r.get("reward_delta")) and r["reward_delta"] > 0)
                for r in after
            )
            heights = [
                r.get("sector_heights") for r in after if r.get("sector_heights") is not None
            ]
            activated |= any(a != b for a, b in zip(heights, heights[1:]))
            covered = any(trace.tic(r["tic"]) == tic + 35 for r in after)
            results.append(
                dict(
                    decision=event["decision_idx"],
                    tic=tic,
                    proxy_change=bool(activated),
                    post_window_covered=covered,
                )
            )
        metrics["uses"] = results
        if any(r["proxy_change"] for r in results):
            status, note = (
                "behavior_contradicted",
                "Geometry/reward changed in the sampled post-use interval. This proxy does not identify the particular interaction or its cause.",
            )
        elif any(r["post_window_covered"] for r in results):
            status, note = (
                "behavior_confirmed",
                "A use was executed with no sampled geometry/reward response over the next 35 tics. Target functionality and intended design are not established.",
            )
    elif category == "BLOCKED_PATH":
        low = [
            e
            for e, r in translations
            if r.get("collision_substep")
            or trace.feedback.get(e["decision_idx"], {}).get("frame_change_pct", 100) < 2
        ]
        metrics["low_displacement_or_frame_change"] = len(low)
        if len(low) >= 2:
            status, note = (
                "behavior_confirmed",
                "At least two translation primitives show low-displacement/frame-change symptoms; the acceleration-sensitive proxy does not establish a defective path.",
            )
        elif (
            len(ratios) >= 2
            and all(v >= 0.60 for v in ratios)
            and not any(r.get("collision_substep") for _, r in translations)
        ):
            status, note = (
                "behavior_contradicted",
                "All measured translation primitives in the window achieved at least 60% of expected displacement without a collision proxy.",
            )
    elif category == "NAVIGATION_CONFUSION":
        counts = Counter(entries)
        rotation = sum(
            abs((b["angle_deg"] - a["angle_deg"] + 180) % 360 - 180)
            for a, b in zip(points, points[1:])
            if all(number(r.get("angle_deg")) for r in (a, b))
        )
        progress = bfs[0] - bfs[-1] if len(measured) == len(points) and len(bfs) > 1 else None
        metrics.update(
            max_cell_visits=max(counts.values(), default=0),
            yaw_sum_deg=rotation,
            measured_bfs_reduction=progress,
        )
        if (
            max(counts.values(), default=0) >= 3
            or warnings
            or (rotation >= 360 and number(progress) and progress < 64)
        ):
            status, note = (
                "behavior_confirmed",
                "Repeated sampled cell entries, a repeat warning, or measured rotation without BFS progress occurred. Purposeful exploration remains a possible explanation.",
            )
        elif (
            number(progress)
            and progress >= 128
            and len(entries) == len(set(entries))
            and all(a >= b for a, b in zip(bfs, bfs[1:]))
        ):
            status, note = (
                "behavior_contradicted",
                "Measured BFS distance improved monotonically by at least 128 units without sampled cell reentries.",
            )
    elif category == "UNCLEAR_OBJECTIVE":
        distances = [
            r["goal_dist_euclidean"] for r in points if number(r.get("goal_dist_euclidean"))
        ]
        visible = [
            r
            for r in points
            if number(r.get("goal_visible_pixels")) and r["goal_visible_pixels"] >= 25
        ]
        pickup = any(r.get("armor", 0) > trace.initial.get("armor", 0) for r in rows)
        metrics.update(
            min_goal_distance=min(distances) if distances else None,
            visible_goal_samples=len(visible),
            pickup_observed=pickup,
        )
        since_visible = [
            r for r in points if visible and trace.tic(r["tic"]) >= trace.tic(visible[0]["tic"])
        ]
        approach_distances = [
            r["goal_dist_euclidean"] for r in since_visible if number(r.get("goal_dist_euclidean"))
        ]
        direct = (
            len(approach_distances) == len(since_visible)
            and len(approach_distances) >= 2
            and approach_distances[-1] < approach_distances[0]
            and all(a >= b for a, b in zip(approach_distances, approach_distances[1:]))
        )
        if visible and pickup and direct and trace.manifest.get("outcome") == "success":
            status, note = (
                "behavior_contradicted",
                "The visible armor goal was collected and the objective completed in this window.",
            )
        elif len(set(entries)) >= 4 and len(distances) == len(points) and min(distances) > 128:
            status, note = (
                "behavior_confirmed",
                "At least four sampled cells were explored without approaching within 128 units of the tracked goal. This cannot establish that the objective is unclear to humans.",
            )
        elif (
            visible
            and hi - trace.tic(visible[0]["tic"]) >= 105
            and not pickup
            and len(distances) == len(points)
            and distances[-1] >= distances[0]
        ):
            status, note = (
                "behavior_confirmed",
                "A tracked visible goal was not approached/collected over the supported interval; reasons remain uncertain.",
            )
    elif category == "VISUAL_AMBIGUITY":
        attempts = [
            e
            for e in events
            if e.get("event") == "locate" and "armor" in e.get("concept", "").lower()
        ]
        bad = []
        for event in attempts:
            frame_tic = trace.frame_tic(event["decision_idx"])
            before = [
                r
                for r in trace.rows
                if frame_tic is not None
                and trace.tic(r.get("tic")) is not None
                and trace.tic(r["tic"]) <= frame_tic
            ]
            observed = before[-1] if before else None
            if (
                observed
                and (observed.get("goal_visible_pixels") or 0) >= 25
                and (
                    not event.get("found")
                    or all(m.get("confidence", 0) < 0.40 for m in event.get("matches", []))
                )
            ):
                bad.append(event["decision_idx"])
        metrics["low_confidence_goal_lookup_steps"] = bad
        if bad:
            status, note = (
                "behavior_confirmed",
                "A goal-specific lookup was empty/low-confidence while tracked armor pixels were recorded. Candidate confidence alone never disproves ambiguity or verifies identity.",
            )
    elif category == "MECHANIC_UNCLEAR":
        hazards = [
            r
            for r in rows
            if r.get("damage_source") in {"hazard_floor", "poison_pickup"}
            and r.get("health_loss", 0) >= 15
        ]
        metrics["attributed_hazard_events"] = len(hazards)
        if hazards:
            status, note = (
                "behavior_confirmed",
                "Explicit hazard/poison-attributed health loss was recorded. Unattributed combat health loss is excluded.",
            )
        else:
            identified = [
                line
                for sector in trace.geometry.get("sectors", [])
                for line in sector.get("lines", [])
                if line.get("interactive") is True and line.get("closed") is True
            ]
            uses = [e for e in primitives if e.get("requested", {}).get("type") == "use"]
            contacts = [
                row
                for _, row in translations
                if row.get("collision_substep")
                and all(number(row.get(key)) for key in ("pos_x", "pos_y"))
                and any(
                    distance_to_segment((row["pos_x"], row["pos_y"]), line) <= 96
                    for line in identified
                )
            ]
            metrics["identified_closed_door_contacts"] = len(contacts)
            if identified and not uses and len(contacts) >= 2:
                status, note = (
                    "behavior_confirmed",
                    "At least two collision-proxy translations occurred near an explicitly identified closed interactive line, with no use in the evidence window. This verifies the control symptom, not a defective door.",
                )
            elif any(row.get("door_or_switch_activated") for row in rows) and uses:
                status, note = (
                    "behavior_contradicted",
                    "A use and sector-change proxy were recorded in the window. The specific target and design cause remain unverified.",
                )
    elif category == "DEAD_END":
        branch = branch_observation(trace, points)
        metrics["coarse_topology"] = branch
        if (
            branch
            and branch["monotonic_goal_progress"]
            and (branch["coarse_bfs_reduction"] or 0) >= 128
        ):
            status, note = (
                "behavior_contradicted",
                "The sampled trace made monotonic measured coarse-graph progress toward the goal by at least 128 units.",
            )
        elif branch and branch["coarse_goal_free_branch_dwell_tics"] >= 70:
            status, note = (
                "behavior_confirmed",
                "Sampled occupancy in a goal-free degree-one coarse branch persisted for at least 70 tics. An intentional maze branch remains a plausible cause.",
            )
    elif category in {"SOFTLOCK_SUSPECTED", "UNREACHABLE_SUSPECTED"}:
        note = "The available coarse static graph cannot establish dynamic reachability, required keys or interactive state. Missing/no-path coarse BFS is not proof of a softlock or defect."
        metrics["dynamic_reachability_verified"] = False
    else:
        loss = max(
            (a.get("health", 0) - b.get("health", 0) for a, b in zip(points, points[1:])), default=0
        )
        metrics["observed_health_loss"] = loss
        if warnings or loss >= 10:
            status, note = (
                "behavior_confirmed",
                "The generic OTHER guard found a stuck warning or at least 10 health loss. This confirms only that symptom, not arbitrary accompanying claims or a design cause.",
            )
        else:
            note = "No generic warning/health-loss predicate was observed; arbitrary OTHER claims remain unverifiable."
    return status, note, metrics


def triage(trace, issue, comparison_manifests):
    if issue.trace_verification_status != "behavior_confirmed":
        return None
    _, _, rows, primitives, events = trace.window(issue.tic_range)
    faults = [
        e
        for e in events
        if e.get("event") == "env_fault_blocked_use"
        and e.get("fault") == "unresponsive_use_trigger"
    ]
    m = trace.manifest
    if (
        m.get("scenario_id") == "probe_broken_door"
        and m.get("is_fault_probe") is True
        and m.get("fault_injection") == "unresponsive_use_trigger"
        and faults
    ):
        return "CONFIRMED_INJECTED_FAULT"
    # Only explicitly identified interactive lines qualify; ordinary walls are not doors.
    doors = [
        line
        for sector in trace.geometry.get("sectors", [])
        for line in sector.get("lines", [])
        if line.get("interactive") is True
    ]
    if issue.category in {"INTERACTION_NO_EFFECT", "BLOCKED_PATH"} and doors:
        for event in primitives:
            if event.get("requested", {}).get("type") != "use":
                continue
            before = [
                r
                for r in trace.rows
                if trace.tic(r.get("tic")) is not None
                and trace.tic(r["tic"]) <= event["tic_start"]
                and all(number(r.get(k)) for k in ("pos_x", "pos_y", "angle_deg"))
            ]
            if not before:
                continue
            row = before[-1]
            line = min(doors, key=lambda d: distance_to_segment((row["pos_x"], row["pos_y"]), d))
            distance = distance_to_segment((row["pos_x"], row["pos_y"]), line)
            normal = math.degrees(math.atan2(line["y2"] - line["y1"], line["x2"] - line["x1"])) + 90
            angle = min(
                abs((row["angle_deg"] - normal + 180) % 360 - 180),
                abs((row["angle_deg"] - normal) % 360 - 180),
            )
            issue.verifier_metrics.update(
                interaction_distance=distance, heading_off_normal_deg=angle
            )
            if distance > 64 or angle > 45:
                return "AGENT_CONTROL_MISALIGNMENT"
            if distance <= 64 and angle <= 30:
                return "CANDIDATE_PROGRESSION_BLOCKER_NEEDS_HUMAN_CHECK"
    valid = []
    for other in comparison_manifests:
        if (
            other.get("scenario_id") == m.get("scenario_id")
            and other.get("seed") == m.get("seed")
            and not other.get("is_fault_probe")
            and other.get("evaluation_group") != "carryover"
            and not other.get("carryover")
            and m.get("evaluation_group") != "carryover"
            and not m.get("carryover")
            and not pairing_errors(m, other)
        ):
            valid.append(other)
    levels = {other["level"]: other for other in valid}
    if (
        issue.level == 1
        and issue.category in {"UNCLEAR_OBJECTIVE", "MECHANIC_UNCLEAR", "VISUAL_AMBIGUITY"}
        and all(levels.get(level, {}).get("outcome") == "success" for level in (2, 3))
    ):
        return "CANDIDATE_SIGNPOSTING_OR_CONVENTION_GAP"
    if (
        issue.level in (1, 2)
        and issue.category in {"NAVIGATION_CONFUSION", "DEAD_END"}
        and (levels.get(3, {}).get("outcome") == "success" or issue.scenario_id == "my_way_home")
    ):
        return "CANDIDATE_NAVIGATION_FRICTION_OR_INTENTIONAL_MAZE"
    return None


def verify_run(directory, *, findings=None, reviewer=None, comparisons=()):
    trace = Trace(directory)
    candidates = (
        read_json(trace.directory / "issues.json")
        if (trace.directory / "issues.json").exists()
        else []
    )
    candidates = [{**c, "source": c.get("source", "pilot_inline")} for c in candidates]
    if findings is not None:
        candidates += findings
    candidate_sources = []
    if reviewer:
        reviewer = Path(reviewer)
        sidecar = reviewer.parent / "reviewer_status.json"
        if sidecar.exists():
            status = read_json(sidecar)
            if (
                status.get("source_run_id") != trace.manifest["run_id"]
                or status.get("status") != "completed"
            ):
                raise ValueError("Reviewer does not match this finalized run")
            if status.get("reviewer_sha256") and status["reviewer_sha256"] != digest(reviewer):
                raise ValueError("Reviewer output changed after generation")
        else:
            trace.limitations.append(
                "Supplied candidate file has no run-bound Reviewer status sidecar; provenance is unverified."
            )
        candidate_sources.append(dict(path=str(reviewer), sha256=digest(reviewer)))
        result = read_json(reviewer)
        candidates += [{**f, "source": "reviewer_post"} for f in result.get("findings", [])]
    records = []
    for index, candidate in enumerate(candidates, 1):
        record = normalize(trace, candidate, index)
        record.tic_range = trace.evidence_range(record.evidence_steps)
        records.append(record)
    records = deduplicate(records)
    for record in records:
        if not trace.finalized or record.tic_range is None:
            record.verifier_rule = "evidence_unavailable"
            record.verifier_notes = (
                "Source is unfinalized or cited observed frames/timing are missing."
            )
        else:
            record.trace_verification_status, record.verifier_notes, record.verifier_metrics = rule(
                trace, record
            )
            record.verifier_rule = "check_" + record.category.lower()
            record.causal_hypothesis = triage(trace, record, comparisons)
        record.evidence_limitations = list(trace.limitations) + [
            "Behavior confirmation is not a game-defect verdict; human design intent and playtesting are not established."
        ]
    return dict(
        run_id=trace.manifest["run_id"],
        source_directory=str(trace.directory),
        source_sha256=trace.source_hashes,
        verifier_source_sha256={
            name: digest(Path(__file__).with_name(name))
            for name in ("verify.py", "trace.py", "topology.py", "schemas.py")
        },
        candidate_sources=candidate_sources,
        recording_provenance=trace.manifest.get("recording_provenance", "original_native"),
        is_fault_probe=bool(trace.manifest.get("is_fault_probe")),
        issues=[r.model_dump() for r in records],
        counts={
            s: sum(r.trace_verification_status == s for r in records)
            for s in ("behavior_confirmed", "behavior_contradicted", "unverifiable")
        },
    )


def write_verification(directory, output, **kwargs):
    result = verify_run(directory, **kwargs)
    write_json(output, result)
    return result
