"""Paired RQ3 evaluation, kept separate from ordinary information-level effects."""

from collections import defaultdict

from autoqa.eval.aggregate import pairing_errors
from autoqa.eval.metrics import episode_metrics, number, read_json, read_lines


def first_new_room(directory):
    rows = read_lines(directory / "telemetry.jsonl")
    initial = next((r for r in rows if r.get("event") == "init"), {})
    key = "sector_id"
    if initial.get(key) is None:
        return None
    next_room = next(
        (
            r
            for r in rows
            if r.get(key) is not None
            and r[key] != initial[key]
            and number(r.get("tic"))
            and r["tic"] >= initial["tic"]
        ),
        None,
    )
    return next_room["tic"] - initial["tic"] if next_room else None


def paired_results(directories):
    groups = defaultdict(dict)
    for directory in directories:
        m = read_json(directory / "manifest.json")
        if m.get("evaluation_group") != "carryover" or m.get("is_fault_probe"):
            raise ValueError("Only explicit carry-over episodes belong in RQ3")
        if m.get("attempt") not in (1, 2) or not m.get("pair_id"):
            raise ValueError("Missing carry-over pair identity")
        if m["attempt"] in groups[m["pair_id"]]:
            raise ValueError("Duplicate carry-over attempt")
        groups[m["pair_id"]][m["attempt"]] = (directory, m)
    output = []
    for pair, attempts in sorted(groups.items()):
        result = dict(pair_id=pair, status="incomplete", deltas=None)
        if set(attempts) == {1, 2}:
            a, b = (attempts[i] for i in (1, 2))
            errors = pairing_errors(a[1], b[1])
            brief = b[1].get("carryover", {})
            if brief.get("source_run_id") != a[1]["run_id"]:
                errors.append("carryover_source_mismatch")
            from autoqa.agent.carryover import extract

            expected = extract(a[0])
            if (
                brief.get("source_sha256") != expected["source_sha256"]
                or brief.get("brief_sha256") != expected["brief_sha256"]
            ):
                errors.append("carryover_provenance_mismatch")
            ma, mb = (episode_metrics(r[0]) for r in (a, b))
            if not all(m["included_in_gameplay"] for m in (ma, mb)):
                errors.append("invalid_attempt_outcome")
            values = []
            for directory, m in (a, b):
                metrics = episode_metrics(directory)
                values.append(
                    dict(
                        completed=metrics["completed"],
                        progress_fraction=metrics["progress_fraction"],
                        tics_to_first_new_room=first_new_room(directory),
                        stuck_warning_count=metrics["stuck_warning_count"],
                        cost_usd=metrics["cost_usd"],
                    )
                )
            result.update(
                status="protocol_mismatch" if errors else "complete",
                pairing_errors=errors,
                attempts=[
                    dict(run_id=r[1]["run_id"], **v) for r, v in zip((a, b), values, strict=True)
                ],
                deltas={
                    k: values[1][k] - values[0][k]
                    if not errors and all(number(v[k]) for v in values)
                    else None
                    for k in values[0]
                },
            )
        output.append(result)
    return output
