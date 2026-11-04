"""Sequential, resumable M3 sweeps. Preparation and analysis never make API calls."""

import hashlib
import subprocess
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from autoqa.config import RunConfig, StrictModel
from autoqa.eval.aggregate import pairing_errors, summarize, write_summary
from autoqa.eval.metrics import episode_metrics, read_json
from autoqa.eval.plots import heatmaps
from autoqa.io import digest, write_json


class ScenarioSweep(StrictModel):
    levels: list[Literal[1, 2, 3]] = Field(min_length=1)
    max_decisions: int = Field(ge=1, le=1000)
    max_tics: int = Field(ge=1, le=4200)

    @model_validator(mode="after")
    def unique_levels(self):
        if len(self.levels) != len(set(self.levels)):
            raise ValueError("Duplicate information level")
        return self


class SweepConfig(StrictModel):
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    seeds: list[int] = Field(min_length=1)
    scenarios: dict[
        Literal[
            "my_way_home", "health_gathering_supreme", "deadly_corridor_nav", "freedoom2_map01"
        ],
        ScenarioSweep,
    ]
    model: Literal["muse-spark-1.3", "muse-spark-1.3-contributor"] = "muse-spark-1.3"
    effort: Literal["minimal", "low", "medium"] = "low"
    grounding_mode: Literal["sam_text", "muse_box_sam"] = "muse_box_sam"
    max_usd: float = Field(gt=0, allow_inf_nan=False)
    max_total_usd: float = Field(gt=0, allow_inf_nan=False)
    wall_seconds: float = Field(default=900.0, gt=0, allow_inf_nan=False)
    request_timeout: float = Field(default=45.0, gt=0, allow_inf_nan=False)
    max_output_tokens: int = Field(default=3000, ge=3000, le=8000)

    @model_validator(mode="after")
    def unique_seeds_and_scenarios(self):
        if len(set(self.seeds)) != len(self.seeds) or any(
            not 0 <= s <= 2**32 - 1 for s in self.seeds
        ):
            raise ValueError("Seeds must be unique uint32 values")
        if not self.scenarios:
            raise ValueError("At least one scenario required")
        if self.max_total_usd < self.max_usd:
            raise ValueError("Total threshold must cover at least one episode threshold")
        return self

    def runs(self):
        common = self.model_dump(exclude={"name", "seeds", "scenarios", "max_total_usd"})
        return [
            RunConfig.model_validate(
                dict(
                    **common,
                    scenario=scenario,
                    level=level,
                    seed=seed,
                    max_decisions=spec.max_decisions,
                    max_tics=spec.max_tics,
                )
            )
            for scenario, spec in self.scenarios.items()
            for seed in self.seeds
            for level in spec.levels
        ]


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node):
    pairs = loader.construct_pairs(node, deep=True)
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate YAML key: {key}")
        result[key] = value
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def load_config(path):
    return SweepConfig.model_validate(yaml.load(Path(path).read_text(), Loader=UniqueLoader))


def source_hashes(root):
    paths = (
        sorted((root / "autoqa").rglob("*.py"))
        + sorted((root / "prompts").glob("*.md"))
        + sorted((root / "scenarios").rglob("briefing_l2.md"))
    )
    paths += [root / p for p in ("configs/calibration.json", "pyproject.toml", "uv.lock")]
    return {str(p.relative_to(root)): digest(p) for p in paths}


def prepare_sweep(config_path, output, *, root=Path.cwd()):
    config_path, output, root = Path(config_path), Path(output), Path(root).resolve()
    cfg = load_config(config_path)
    configs = cfg.runs()
    output.mkdir(parents=True, exist_ok=False)
    plan = dict(
        schema_version=1,
        name=cfg.name,
        status="prepared_no_api_calls",
        config=cfg.model_dump(),
        config_sha256=digest(config_path),
        source_sha256=source_hashes(root),
        git_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        runtime_dirty=bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()
        ),
        planned_episodes=len(configs),
        sum_episode_thresholds_usd=sum(c.max_usd for c in configs),
        sum_episode_wall_limits_seconds=sum(c.wall_seconds for c in configs),
        max_total_usd=cfg.max_total_usd,
        cost_warning="Usage-based stop thresholds, not provider hard caps; failed-request charges can be unknown",
        slots=[
            dict(
                scenario=c.scenario,
                level=c.level,
                seed=c.seed,
                config=c.model_dump(),
                status="pending",
                run_directory=None,
            )
            for c in configs
        ],
    )
    write_json(output / "sweep_manifest.json", plan)
    return plan


def analyze_runs(directories, output, *, expected=None):
    directories, output = [Path(p).resolve() for p in directories], Path(output)
    output.mkdir(parents=True, exist_ok=False)
    summary = summarize(directories, expected=expected)
    for row in summary["episodes"]:
        name = hashlib.sha256(row["directory"].encode()).hexdigest()[:12]
        write_json(
            output / f"metrics_{name}.json", {k: v for k, v in row.items() if k != "directory"}
        )
    summary["heatmaps"] = heatmaps(directories, output / "heatmaps")
    write_summary(output, summary)
    return summary


def execute_sweep(output, *, approved=False, root=Path.cwd(), runner=None):
    if not approved:
        raise ValueError("Live sweep requires explicit approval (--execute)")
    lock = Path(output) / ".execution.lock"
    with lock.open("x"):
        try:
            return _execute_sweep(output, root=root, runner=runner)
        finally:
            lock.unlink()


def _execute_sweep(output, *, root, runner):
    output, root = Path(output), Path(root).resolve()
    path = output / "sweep_manifest.json"
    plan = read_json(path)
    if source_hashes(root) != plan["source_sha256"]:
        raise ValueError("Source/prompt/calibration changed since preparation; use a fresh plan")
    cfg = SweepConfig.model_validate(plan["config"])
    if [s["config"] for s in plan["slots"]] != [c.model_dump() for c in cfg.runs()]:
        raise ValueError("Sweep slots differ from the frozen configuration")
    if any(s["status"] == "running" for s in plan["slots"]):
        raise ValueError(
            "An interrupted running slot may have incurred charges. Resolve it manually; never auto-repeat paid calls."
        )
    for slot in plan["slots"]:
        if slot["status"] == "recorded":
            manifest = read_json(Path(slot["run_directory"]) / "manifest.json")
            if manifest["config"] != slot["config"]:
                raise ValueError("Recorded run does not match the planned slot")
            episode_metrics(Path(slot["run_directory"]))  # verify source artifacts before resuming
    if runner is None:
        from autoqa.run import run_episode

        runner = run_episode
    plan["status"] = "running"
    write_json(path, plan)
    try:
        for slot in plan["slots"]:
            if slot["status"] != "pending":
                continue
            spent = sum(s.get("recorded_cost_usd", 0) or 0 for s in plan["slots"])
            if any(s.get("cost_unknown", False) for s in plan["slots"]):
                plan["status"] = "stopped_unknown_cost"
                break
            if spent + cfg.max_usd > cfg.max_total_usd + 1e-9:
                plan["status"] = "stopped_total_cost_threshold"
                break
            if source_hashes(root) != plan["source_sha256"]:
                raise ValueError("Source changed during sweep; no further episode started")
            slot["status"] = "running"
            write_json(path, plan)
            directory, manifest = runner(RunConfig.model_validate(slot["config"]), root=root)
            slot.update(
                status="recorded",
                run_directory=str(Path(directory).resolve()),
                outcome=manifest["outcome"],
                recorded_cost_usd=manifest.get("total_usd"),
                cost_unknown=manifest.get("total_usd") is None,
            )
            # A completed episode can still contain failed attempts with unknown charges.
            metrics = episode_metrics(directory)
            slot["unknown_failed_request_charges"] = metrics[
                "unknown_failed_request_charges_possible"
            ]
            write_json(Path(directory) / "metrics.json", metrics)
            errors = []
            for previous in plan["slots"]:
                if previous is slot or previous["status"] != "recorded":
                    continue
                if (previous["scenario"], previous["seed"]) == (slot["scenario"], slot["seed"]):
                    errors.extend(
                        pairing_errors(
                            read_json(Path(previous["run_directory"]) / "manifest.json"), manifest
                        )
                    )
            if errors:
                slot["pairing_errors"] = sorted(set(errors))
                plan["status"] = "stopped_pairing_error"
                write_json(path, plan)
                break
            write_json(path, plan)
        else:
            plan["status"] = "finished"
    except BaseException as error:
        plan["status"] = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
        plan["error_type"] = type(error).__name__
        raise
    finally:
        plan["recorded_cost_usd"] = sum(s.get("recorded_cost_usd", 0) or 0 for s in plan["slots"])
        plan["remaining_slots"] = sum(s["status"] == "pending" for s in plan["slots"])
        write_json(path, plan)
    directories = [Path(s["run_directory"]) for s in plan["slots"] if s["run_directory"]]
    # Numbered analyses preserve earlier partial results on resume.
    versions = [p for p in output.glob("analysis_*") if p.is_dir()]
    analysis = output / f"analysis_{len(versions):03d}"
    summary = analyze_runs(directories, analysis, expected=plan["slots"])
    # Required top-level outputs always refer to the latest recorded snapshot.
    write_summary(output, summary)
    plan["analysis_directory"] = str(analysis)
    plan["pairing_status"] = (
        "complete"
        if all(p["status"] in {"complete", "not_scheduled"} for p in summary["paired_comparisons"])
        else "incomplete"
    )
    write_json(path, plan)
    return plan
