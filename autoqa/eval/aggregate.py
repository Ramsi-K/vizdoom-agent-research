"""Paired comparisons and uncertainty estimates; missing evidence stays missing."""

import csv
import json
import math
import random
from statistics import mean, stdev

from autoqa.io import write_json

from .metrics import METRICS, episode_metrics, number, read_json


def estimate(values, *, binary=False):
    values = [v for v in values if number(v)]
    n = len(values)
    result = dict(
        n=n,
        mean=mean(values) if n else None,
        sd=stdev(values) if n > 1 else None,
        ci95=None,
        ci_method=None,
    )
    if binary and n:
        p, z = mean(values), 1.959963984540054
        center = (p + z * z / (2 * n)) / (1 + z * z / n)
        half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
        result.update(ci95=[max(0, center - half), min(1, center + half)], ci_method="wilson")
    elif n > 1:
        rng = random.Random(101)
        draws = sorted(mean(rng.choices(values, k=n)) for _ in range(2000))
        result.update(ci95=[draws[49], draws[1949]], ci_method="percentile_bootstrap_2000_seed101")
    return result


def classify(levels):
    """Apply the documented five rules in explicit priority order, without filling gaps."""
    if set(levels) != {1, 2, 3}:
        return dict(classification=None, status="missing_valid_level", causal_claim=False)
    a, b, c = (levels[i] for i in (1, 2, 3))
    wins = tuple(bool(v["completed"]) for v in (a, b, c))
    p = [v["progress_fraction"] for v in (a, b, c)]
    delta21 = p[1] - p[0] if all(number(v) for v in p[:2]) else None
    delta32 = p[2] - p[1] if all(number(v) for v in p[1:]) else None
    label = None
    if all(wins) and delta21 is not None and delta21 < 0.20:
        label = "SELF_EVIDENT_TO_AGENT"
    elif wins[1] and not wins[2]:
        label = "NEGATIVE_MAP_TRANSFER"
    elif wins[1] and wins[2] and (not wins[0] or (delta21 is not None and delta21 >= 0.30)):
        label = "DISCOVERABILITY_SENSITIVE"
    elif wins[2] and ((not wins[0] and not wins[1]) or (delta32 is not None and delta32 >= 0.30)):
        label = "NAVIGATION_SENSITIVE"
    elif not any(wins) and number(p[2]) and p[2] < 0.80:
        label = "PERSISTENT_FAILURE_L1_TO_L3"
    return dict(
        classification=label,
        status="classified" if label else "insufficient_progress_or_uncovered_outcome_pattern",
        causal_claim=False,
    )


def pairing_errors(a, b):
    errors = []
    for field in (
        "source_sha256",
        "iwad_sha256",
        "scenario_cfg_sha256",
        "scenario_wad_sha256",
        "calibration_sha256",
        "vizdoom_version",
    ):
        if not a.get(field) or a.get(field) != b.get(field):
            errors.append(f"missing_or_different_{field}")
    ac, bc = a.get("config", {}).copy(), b.get("config", {}).copy()
    ac.pop("level", None)
    bc.pop("level", None)
    if not ac or ac != bc:
        errors.append("missing_or_different_settings")
    sa, sb = a.get("spawn_state", {}), b.get("spawn_state", {})
    for key in ("pos_x", "pos_y", "pos_z", "angle_deg", "tic"):
        x, y = sa.get(key), sb.get(key)
        if not number(x) or not number(y):
            errors.append(f"missing_spawn_{key}")
        else:
            distance = abs((x - y + 180) % 360 - 180) if key == "angle_deg" else abs(x - y)
            if distance > 0.01:
                errors.append(f"different_spawn_{key}")
    if a.get("level", 0) > 1 and b.get("level", 0) > 1:
        if not a.get("briefing_sha256") or a.get("briefing_sha256") != b.get("briefing_sha256"):
            errors.append("different_or_missing_briefing")
    return errors


def summarize(directories, *, expected=None):
    records = []
    slots = {}
    for directory in directories:
        manifest = read_json(directory / "manifest.json")
        metrics = episode_metrics(directory)
        key = (metrics["scenario_id"], metrics["seed"], metrics["level"])
        if key in slots:
            raise ValueError(
                f"Duplicate scenario/seed/level: {key}; do not pool protocol iterations"
            )
        record = dict(directory=str(directory), metrics=metrics, manifest=manifest)
        slots[key] = record
        records.append(record)
    expected = expected or [
        dict(
            scenario=r["metrics"]["scenario_id"],
            seed=r["metrics"]["seed"],
            level=r["metrics"]["level"],
        )
        for r in records
    ]
    scenarios = sorted({e["scenario"] for e in expected})
    groups, comparisons, classifications = [], [], []
    for scenario in scenarios:
        planned = [e for e in expected if e["scenario"] == scenario]
        levels = sorted({e["level"] for e in planned})
        rows = [r for r in records if r["metrics"]["scenario_id"] == scenario]
        # Prefixes may differ by level, but must stay frozen across seeds within a level.
        for level in levels:
            protocols = set()
            for record in rows:
                if record["metrics"]["level"] != level:
                    continue
                manifest = record["manifest"]
                settings = manifest.get("config", {}).copy()
                settings.pop("seed", None)
                settings.pop("level", None)
                fingerprint = dict(
                    settings=settings,
                    **{
                        k: manifest.get(k)
                        for k in (
                            "prompt_sha256",
                            "source_sha256",
                            "iwad_sha256",
                            "scenario_cfg_sha256",
                            "scenario_wad_sha256",
                            "calibration_sha256",
                            "vizdoom_version",
                        )
                    },
                )
                protocols.add(json.dumps(fingerprint, sort_keys=True))
            if len(protocols) > 1:
                raise ValueError(f"Mixed protocol versions/settings for {scenario} L{level}")
            cell = [r["metrics"] for r in rows if r["metrics"]["level"] == level]
            valid = [m for m in cell if m["included_in_gameplay"]]
            groups.append(
                dict(
                    scenario=scenario,
                    level=level,
                    planned=sum(e["level"] == level for e in planned),
                    recorded=len(cell),
                    included=len(valid),
                    excluded=len(cell) - len(valid),
                    completion_rate=mean(m["completed"] for m in valid) if valid else None,
                    metrics={
                        k: estimate([m[k] for m in valid], binary=k == "completed") for k in METRICS
                    },
                    recorded_cost_usd=sum(m["cost_usd"] for m in cell if number(m["cost_usd"])),
                    recorded_cost_unknown_count=sum(not number(m["cost_usd"]) for m in cell),
                )
            )
        seeds = sorted({e["seed"] for e in planned})
        for lower, higher in ((1, 2), (2, 3)):
            paired, rejected = [], []
            planned_seeds = [
                seed
                for seed in seeds
                if all(
                    any(e["seed"] == seed and e["level"] == level for e in planned)
                    for level in (lower, higher)
                )
            ]
            for seed in planned_seeds:
                pair = [slots.get((scenario, seed, level)) for level in (lower, higher)]
                if any(r is None for r in pair):
                    rejected.append(dict(seed=seed, reasons=["missing_run"]))
                    continue
                if any(not r["metrics"]["included_in_gameplay"] for r in pair):
                    rejected.append(dict(seed=seed, reasons=["excluded_outcome"]))
                    continue
                errors = pairing_errors(*(r["manifest"] for r in pair))
                if errors:
                    rejected.append(dict(seed=seed, reasons=errors))
                    continue
                paired.append(
                    dict(
                        seed=seed,
                        deltas={
                            k: pair[1]["metrics"][k] - pair[0]["metrics"][k]
                            if all(number(r["metrics"][k]) for r in pair)
                            else None
                            for k in METRICS
                        },
                    )
                )
            comparisons.append(
                dict(
                    scenario=scenario,
                    comparison=f"L{higher}-L{lower}",
                    planned_pairs=len(planned_seeds),
                    matched_pairs=len(paired),
                    status="not_scheduled"
                    if not planned_seeds
                    else "complete"
                    if len(paired) == len(planned_seeds)
                    else "incomplete",
                    metrics={k: estimate([p["deltas"][k] for p in paired]) for k in METRICS},
                    pairs=paired,
                    rejected=rejected,
                )
            )
        seed_labels = []
        for seed in seeds:
            triple = [slots.get((scenario, seed, level)) for level in (1, 2, 3)]
            available = {
                r["metrics"]["level"]: r["metrics"]
                for r in triple
                if r and r["metrics"]["included_in_gameplay"]
            }
            errors = (
                pairing_errors(triple[0]["manifest"], triple[1]["manifest"])
                + pairing_errors(triple[1]["manifest"], triple[2]["manifest"])
                if len(available) == 3
                else []
            )
            label = (
                dict(classification=None, status="protocol_or_spawn_mismatch", causal_claim=False)
                if errors
                else classify(available)
            )
            seed_labels.append(dict(seed=seed, **label, pairing_errors=errors))
        labels = {r["classification"] for r in seed_labels}
        consensus = next(iter(labels)) if len(labels) == 1 and None not in labels else None
        classifications.append(
            dict(
                scenario=scenario,
                classification=consensus,
                status="unanimous_paired_seeds"
                if consensus
                else "insufficient_or_mixed_paired_evidence",
                seeds=seed_labels,
                interpretation="Diagnostic information sensitivity of bundled protocols, not a design defect or human-playability claim",
            )
        )
    return dict(
        schema_version=1,
        groups=groups,
        paired_comparisons=comparisons,
        classifications=classifications,
        episodes=[dict(directory=r["directory"], **r["metrics"]) for r in records],
        recorded_cost_usd=sum(
            r["metrics"]["cost_usd"] for r in records if number(r["metrics"]["cost_usd"])
        ),
        uncertainty="Wilson 95% completion intervals; deterministic percentile bootstrap for means/deltas with n>=2. Descriptive at small n; no causal identification.",
        limitations=[
            "Missing metrics retain null and per-metric sample sizes",
            "No paid sweep implied by offline analysis",
            "Endpoint coverage/path and collision/use proxies are approximate",
            "Unknown charges for failed API requests are not included",
            "Five documented classification rules are not exhaustive; missing L3/progress is not guessed",
        ],
    )


def write_summary(directory, summary):
    write_json(directory / "summary.json", summary)
    rows = []
    for group in summary["groups"]:
        row = {
            k: group[k]
            for k in (
                "scenario",
                "level",
                "planned",
                "recorded",
                "included",
                "excluded",
                "recorded_cost_usd",
                "completion_rate",
            )
        }
        row["classification"] = next(
            c["classification"]
            for c in summary["classifications"]
            if c["scenario"] == group["scenario"]
        )
        for metric, stat in group["metrics"].items():
            for field in ("n", "mean", "sd"):
                row[f"{metric}_{field}"] = stat[field]
            row[f"{metric}_ci95_low"], row[f"{metric}_ci95_high"] = stat["ci95"] or (None, None)
        for comparison in summary["paired_comparisons"]:
            if comparison["scenario"] != group["scenario"]:
                continue
            prefix = comparison["comparison"].lower().replace("-", "_minus_")
            for metric, stat in comparison["metrics"].items():
                row[f"delta_{prefix}_{metric}"], row[f"delta_{prefix}_{metric}_n"] = (
                    stat["mean"],
                    stat["n"],
                )
        rows.append(row)
    with (directory / "summary.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]) if rows else ["scenario", "level"])
        writer.writeheader()
        writer.writerows(rows)
