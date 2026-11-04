"""Published design context is separate from genuinely recorded human measurements."""

from pathlib import Path
from statistics import median

from autoqa.eval.metrics import number, read_json
from autoqa.io import digest

from .trace import safe_file


def human_reference(scenario, *, root=Path.cwd()):
    reference_id = (
        "freedoom2_map01"
        if scenario in {"freedoom2_combat_map01", "probe_broken_door"}
        else scenario
    )
    source = safe_file(root, f"scenarios/{reference_id}/human_reference.json", {".json"})
    result = dict(
        scenario=scenario,
        human_completion_rate=None,
        median_human_tics=None,
        human_runs_recorded=0,
        status="not run",
        documented_design_mechanics=[],
    )
    if not source.exists():
        result["reference_status"] = "no qualitative annotation file"
        return result
    data = read_json(source)
    if data.get("scenario_id") != reference_id:
        raise ValueError("Human reference scenario mismatch")
    result.update(
        reference_scenario=reference_id,
        reference_sha256=digest(source),
        documented_design_mechanics=data.get("documented_design_mechanics", []),
    )
    count = data.get("human_runs_recorded", 0)
    measured = (
        count != 0
        or data.get("human_completion_rate") is not None
        or data.get("median_human_tics") is not None
    )
    if not measured:
        return result
    relative = data.get("human_runs_dir")
    if not relative or type(count) is not int or count <= 0:
        raise ValueError("Non-null human statistics require recorded SPECTATOR runs")
    directory = safe_file(root, relative)
    manifests = sorted(directory.glob("*/manifest.json"))
    if (directory / "manifest.json").exists():
        manifests.append(directory / "manifest.json")
    records = []
    for path in manifests:
        m = read_json(path)
        lmp = path.parent / "episode.lmp"
        mode = m.get("mode", m.get("engine_overrides", {}).get("mode"))
        if (
            mode != "SPECTATOR"
            or not lmp.exists()
            or m.get("episode_lmp_sha256") != digest(lmp)
            or not number(m.get("tics_used"))
            or m["tics_used"] < 0
            or m.get("scenario_id") != reference_id
            or m.get("outcome") in (None, "running")
        ):
            raise ValueError("Human measurements need matching native SPECTATOR provenance")
        records.append(m)
    if len(records) != count:
        raise ValueError("Human run count does not match native recordings")
    completion = sum(m["outcome"] == "success" for m in records) / count
    elapsed = median(m["tics_used"] for m in records)
    if data.get("human_completion_rate") != completion or data.get("median_human_tics") != elapsed:
        raise ValueError("Human statistics differ from their recorded manifests")
    result.update(
        human_completion_rate=completion,
        median_human_tics=elapsed,
        human_runs_recorded=count,
        status="recorded SPECTATOR manifests; human provenance still requires audit",
    )
    return result
