# Auto QA agent for ViZDoom: Meta-powered autonomous playtesting, information-level experiments, and QA reporting

*Project: Generalist Game QA, Meta Global AI Developer Hackathon. Drafted 4 Nov 2026 from current Meta Model API, ViZDoom, and V-JEPA documentation. Bracketed numbers point to the source list in section 14.*

## 10. QA reporting, post-episode Reviewer, and deterministic trace verification (`autoqa/qa/`)

### 10.1 Unified issue schema (`autoqa/qa/schemas.py`) — separating behavioral confirmation from design-defect proof

A central methodological principle of this project (README section 6.4–6.5) is that **an observable gameplay event in an AI trace (such as colliding with a wall, entering a dead end, or failing to open a door) does not automatically prove a game-design defect**. An agent may fail because it stood too far from a door when pressing `use`, because a maze intentionally includes a dead-end branch, or because a blind L1 agent lacks standard genre conventions.

Accordingly, `IssueRecord` (`runs/<run_id>/issues.json`) separates **Trace Behavior Verification** (*Did the physical event described by the model actually happen in the engine telemetry, or did the VLM hallucinate it?*) from **Causal Triage Hypothesis** (*What does cross-level and geometric evidence suggest a human developer should check?*):

```json
{
  "issue_id": "iss_my_way_home_L1_s101_01",
  "run_id": "20261104T120000_my_way_home_L1_s101",
  "scenario_id": "my_way_home",
  "level": 1,
  "seed": 101,
  "source": "merged",
  "category": "DEAD_END",
  "severity": "medium",
  "summary": "Corridor terminates in an unmarked wall with no interactable switch or door.",
  "rationale": "Entered grey corridor at decision 6, reached end wall at decision 9, pressed USE with 0.4% frame change, had to turn 180 degrees to backtrack.",
  "evidence_steps": [6, 7, 8, 9],
  "tic_range": [105, 192],
  "model_confidence": 0.88,
  "trace_verification_status": "behavior_confirmed",
  "verifier_rule": "check_dead_end_occupancy",
  "verifier_notes": "Telemetry confirms player occupied degree-1 cell (-2, 5) for 112 tics with 0 goal objects in branch. Confirms behavioral observation only; does not establish whether the dead end is an unintended defect or intentional maze topology.",
  "verifier_metrics": {
    "cell": [-2, 5],
    "tics_in_dead_end": 112,
    "min_frame_change_pct": 0.4
  },
  "causal_hypothesis": "CANDIDATE_NAVIGATION_FRICTION_OR_INTENTIONAL_MAZE",
  "sensitivity_class": "NAVIGATION_SENSITIVE",
  "evidence_frames": ["frames/step_0008_annotated.png", "frames/step_0009_annotated.png"],
  "replay_clip_path": "clips/iss_my_way_home_L1_s101_01.mp4"
}
```

### 10.2 Post-episode Reviewer (`autoqa/qa/reviewer.py`)

After an episode terminates and `episode.lmp` is flushed, `reviewer.py` performs a single post-hoc QA pass:

1. **Video & log preparation**:
   - Replay `episode.lmp` to generate `review_input.mp4` (`640x480`, H.264, `movflags=+faststart` so the `moov` atom sits at the front and skips server remuxing \[7\], 10 fps with burned top-left caption `Step <d> | Tic <t> | <moves> | dFrame=<pct>%`).
   - Upload `review_input.mp4` via `POST /v1/files` (`purpose="user_data"`, `expires_after={"anchor": "created_at", "seconds": 86400}` so team storage does not accumulate \[4\]).
   - Format a compact text table of all decisions from `events.jsonl` (`decision_idx`, `tic_start..tic_end`, `moves`, `intent`, `frame_change_pct`, `locate` results, `stuck_warning`, and any inline `report_issue` calls). Ground-truth telemetry is **not** included so the Reviewer evaluates from player-observable evidence.
2. **Model invocation**:
   - Call `POST /v1/responses` with `model="muse-spark-1.3"` (or `muse-wonder-1.0` in the M5 ablation), `reasoning={"effort": "high"}`, `max_output_tokens=4000`, `input_file` referencing `file.id`, and `text.format` constrained to the `EpisodeQAReview` JSON schema (`strict: True`, nesting depth 4, `additionalProperties: False`) \[4\]\[5\]\[6\]:
     - `episode_summary`: 1–3 sentence summary of what the agent attempted and where it succeeded or failed.
     - `inferred_objective`: What the Reviewer believes the level's goal was from visual evidence.
     - `findings`: Array of objects with `category` (enum of the 10 categories in section 5.2), `severity` (`low` | `medium` | `high`), `summary` ($\le 300$ chars), `rationale` ($\le 500$ chars), `evidence_steps` (`list[int]`, non-empty), `confidence` (`0..1`), and `recommended_developer_check` ($\le 300$ chars, phrased as a concrete inspection question for a human designer rather than a prescription to add yellow paint).
3. **Keyframe-strip fallback**: If `POST /v1/files` or video inference fails or if `--skip-reviewer-video` is set under the `<12h` cut line (section 0.1), `reviewer.py` automatically falls back to passing up to 24 `input_image` blocks (12 uniformly sampled frames across the episode plus up to 12 frames from `stuck_warning` and inline `report_issue` steps) alongside the compact event log \[3\].
4. **Deduplication**: If a Pilot inline issue and a Reviewer finding share the same `category` (or both belong to `{BLOCKED_PATH, INTERACTION_NO_EFFECT, SOFTLOCK_SUSPECTED}`) and their `tic_range` intervals overlap within $\pm 105$ tics, merge them into a single `IssueRecord` with `source="merged"`, unioning `evidence_steps` and taking the maximum `model_confidence`.

### 10.3 Deterministic trace verifier and causal triage (`autoqa/qa/verify.py`)

To answer **RQ4** (*Are the observations actionable and reproducible?*) without overclaiming that every confirmed struggle is a level bug, `autoqa/qa/verify.py` executes a two-stage check over $W = [\text{start\_tic} - 70, \text{end\_tic} + 70]$:

**Stage 1 — Behavioral Trace Confirmation (`trace_verification_status`)**:
Verifies whether the physical event claimed by the model actually occurred in `telemetry.jsonl` and `state.sectors`:

| Issue category | `behavior_confirmed` condition (the reported struggle/event happened in the trace) | `behavior_contradicted` condition (the model hallucinated or misread the event) |
| --- | --- | --- |
| `INTERACTION_NO_EFFECT` | Agent executed `use` inside $W$, and no sector `ceiling_height`/`floor_height` changed and `reward_delta <= 0` within 35 tics | `door_or_switch_activated == True` or `reward_delta > 0` occurred within 35 tics of the cited `use` action |
| `BLOCKED_PATH` | $\ge 2$ translation primitives inside $W$ had `collision_substep == True` (`displacement_units < 0.20 * expected_free_displacement`) or `frame_change_pct < 2.0`, OR a closed door sector remained closed after `use` | All translation primitives in $W$ achieved $\ge 60\%$ of expected free displacement with zero collisions |
| `DEAD_END` | Player coordinates inside $W$ entered a topological degree-1 branch of the 128-unit walkable graph containing zero goal objects and remained there for $\ge 70$ tics | Player cell in $W$ lies on a simple shortest path toward the goal or has $\ge 2$ distinct exit directions toward the goal |
| `NAVIGATION_CONFUSION` | Inside $W \pm 70$ tics, the agent visited the same `(cell_x, cell_y)` $\ge 3$ times, accumulated $\ge 360^\circ$ total yaw rotation with net `goal_dist_bfs` reduction $< 64$ units, or triggered a `stuck_warning` | Agent made monotonic progress toward the goal inside $W$ ($d_{\text{bfs}}(\text{end}) \le d_{\text{bfs}}(\text{start}) - 128$) with zero cell revisits |
| `UNCLEAR_OBJECTIVE` | Agent explored $\ge 4$ distinct cells without approaching within 128 units of the goal, OR passed within visible range of the goal (`goal_visible_pixels >= 25`) without moving toward or collecting it within 105 tics | Agent moved directly toward the goal upon first sighting (`goal_visible_pixels >= 25`) and completed the objective |
| `VISUAL_AMBIGUITY` | Inside $W$, agent either (a) picked up a poison item or took floor damage while `health` dropped, (b) called `locate` returning `found: false` or `confidence < 0.40` while a target `Label` was visible in `state.labels`, or (c) executed `use` on a non-interactable wall | Target object was segmented with `confidence >= 0.75` and interacted with successfully on the first attempt |
| `MECHANIC_UNCLEAR` | Agent approached within 96 units of a closed door without pressing `use` (attempting to walk through it $\ge 2$ times), or took $\ge 15$ health damage from hazard floors/poison pickups inside $W$ | Agent used the correct mechanic (`use` on door, avoided poison) on first encounter inside $W$ |
| `SOFTLOCK_SUSPECTED` / `UNREACHABLE_SUSPECTED` | Walkable-grid BFS from `(pos_x, pos_y)` at `end_tic` (treating closed doors as impassable if `use` was attempted within 64 units and failed to open them) finds **no path** to the goal | Unobstructed walkable path exists from `(pos_x, pos_y)` to the goal without requiring any broken interaction |
| `OTHER` | Marked `unverifiable` unless `stuck_warning` or health loss $\ge 10$ occurred in $W$ | N/A (`unverifiable`) |

**Stage 2 — Causal Triage Hypothesis (`causal_hypothesis`)**:
For every issue with `trace_verification_status == "behavior_confirmed"`, `verify.py` assigns a non-overclaiming triage label using local geometry and cross-level sweep outcomes:
- `CONFIRMED_INJECTED_FAULT`: Assigned **only** when `scenario.is_fault_probe == True` and `env_fault_blocked_use == True` during $W$.
- `AGENT_CONTROL_MISALIGNMENT`: Assigned when `INTERACTION_NO_EFFECT` or `BLOCKED_PATH` is confirmed in the trace, but telemetry shows the agent pressed `use` while $> 64$ map units from any door/switch linedef or at a heading angle $> 45^\circ$ off the linedef normal, or collided with an ordinary wall pillar while an open doorway was adjacent.
- `CANDIDATE_SIGNPOSTING_OR_CONVENTION_GAP`: Assigned when the behavior is confirmed in L1 (`UNCLEAR_OBJECTIVE`, `MECHANIC_UNCLEAR`, `VISUAL_AMBIGUITY`), but the agent succeeds on the same seed in L2 and L3. Flags the location for human review to decide whether the lack of explicit cueing is an unintended usability gap or an expected genre convention.
- `CANDIDATE_NAVIGATION_FRICTION_OR_INTENTIONAL_MAZE`: Assigned when `DEAD_END` or `NAVIGATION_CONFUSION` is confirmed in L1/L2, and resolved in L3 (or occurs inside a documented maze branch such as `my_way_home`). Flags the location so a level designer can judge whether the routing friction matches intended difficulty.
- `CANDIDATE_PROGRESSION_BLOCKER_NEEDS_HUMAN_CHECK`: Assigned in non-probe runs only when the agent pressed `use` within $\le 64$ map units and $\le 30^\circ$ of a two-sided door/switch linedef and no sector moved (e.g., a locked door whose key was not found or a genuine map script bug), or when static BFS finds zero walkable paths.

### 10.4 Evidence rendering and HTML report (`autoqa/qa/report.py`)

1. **Evidence overlays and replay clips**:
   - For every issue in `issues.json`, `report.py` extracts the cited keyframes (`evidence_steps`), draws any SAM 3.1 masks/boxes returned on those steps using decoded rasters from `meta_sam_parser.decode_mask_to_raster` \[8\], adds a 160x160 top-right mini-map inset showing the ground-truth trajectory and cited location (clearly labelled `Eval Telemetry Overlay`), and calls `autoqa replay <run_dir> --from-tic <start-35> --to-tic <end+35> --size 1280x720 --captions` to produce `clips/<issue_id>.mp4`.
2. **Self-contained HTML report (`report.html`)**:
   - Single-file HTML+CSS report (dark theme, zero external CDN dependencies) generated for both individual runs (`runs/<id>/report.html`) and sweeps (`results/<sweep_id>/report.html`).
   - Sections in `results/<sweep_id>/report.html`:
     1. **Executive Summary & Methodological Guardrails**: Total runs, ordinary gameplay runs vs. separate fault-probe runs, headline findings per RQ1–RQ5, and a prominent callout box explaining that (a) `behavior_confirmed` verifies that the telemetry matches the agent's observation, not that the level design is defective, and (b) autonomous completion rates never substitute for human playtesting.
     2. **Information-Level Comparison Table (L1 vs. L2 vs. L3)**: Completion rate, mean/SD progress fraction, SPL, coverage fraction, revisit rate, `use` precision, and cost per scenario across ordinary gameplay benchmarks, plus paired $\Delta(\text{L2}-\text{L1})$ and $\Delta(\text{L3}-\text{L2})$ and the assigned Information-Sensitivity Classification (section 8.4).
     3. **Controlled Fault-Probe Validation (`probe_broken_door`)**: Separate table reporting whether the Pilot and Reviewer detected the injected `unresponsive_use_trigger` fault and whether `verify.py` isolated it as `CONFIRMED_INJECTED_FAULT`.
     4. **Trajectory & Coverage Heatmaps**: Top-down sector maps per `(scenario, level)` overlaying seed trajectories color-coded by outcome (`green` = success, `amber` = timeout/max\_decisions, `red` = dead/blocked) and 128-unit grid visit density.
     5. **Trace-Verified QA Observation Dossier**: Filterable table of all deduplicated issues showing `issue_id`, `scenario`, `level`, `category`, `severity`, `trace_verification_status` badge (`BEHAVIOR CONFIRMED`, `BEHAVIOR CONTRADICTED`, `UNVERIFIABLE`), `causal_hypothesis` badge, `verifier_notes`, annotated keyframe thumbnail, and embedded `<video>` player for `clips/<issue_id>.mp4`.
     6. **Carry-Over Feedback Results (RQ3)**: Paired Attempt 1 vs. Attempt 2 comparison table and trajectory diffs.
     7. **Human Reference & Design-Intent Context (RQ5)**: Cross-tabulates agent observations against `human_reference.json` documented mechanics (and measured `SPECTATOR` human runs if recorded, or explicitly marks quantitative human metrics as `not run`).
     8. **Model & Cost Telemetry**: Token breakdown (`input`, `cached`, `output`, `reasoning`), cache hit rate, SAM 3.1 calls, latency percentiles, and exact USD spend.
3. **README placeholder filler (`scripts/update_readme.py`)**:
   - Reads `results/<sweep_id>/summary.json` and replaces **only** the four placeholder blocks in `README.md` (`## Demo`, `## Technology Overview`, `## Key Findings`, `## Quick Start`), asserting via `git diff` test that no line in Part II (`## 1.` through `## 7.`) was modified (Rule 5, section 0.2).

## 11. Testing, determinism, and enforcement suite (`tests/`)

`make test` runs `pytest -q` and must complete in under 30 seconds without network access (all live API checks are gated behind `@pytest.mark.live_api` and run via `make test-live`).

| Test file | What it verifies | Milestone gate |
| --- | --- | --- |
| `tests/test_info_barrier.py` | 1. Parses AST of every `.py` file under `autoqa/agent/` and `autoqa/prompts/` and asserts zero imports of `vizdoom`, `autoqa.doom.env`, `autoqa.doom.telemetry`, or `autoqa.eval`.<br>2. Constructs `AgentObservation` and calls `prompts.build()` for L1, L2, and L3; asserts L1 prompt contains the exact verbatim task string from section 5.3, zero L2 briefing substrings, and zero `plan_l3.png` bytes; asserts L2 contains L2 briefing and zero `plan_l3.png`; asserts L3 contains both.<br>3. Runs `client.sanitize_request()` on a synthetic request injected with `"pos_x": 120` and asserts `InformationLeakError` is raised. | M1, M2 |
| `tests/test_no_scripted_policy.py` | 1. AST check confirming `DoomEnv.execute` is called at only one site in `autoqa/agent/pilot.py` inside the `item.name == "act"` branch.<br>2. Feeds a mock model sequence containing 3 identical zero-change actions to `Pilot.run_episode()` and asserts `guards.py` appends the text warning to the prompt without altering or inserting any `act` tool call.<br>3. Verifies that `EnvironmentFaultInjector` raises `RuntimeError` if enabled on any scenario with `is_fault_probe == False`, ensuring ordinary gameplay runs never modify `Button.USE` or any other button. | M1, M4 |
| `tests/test_schemas_and_tools.py` | 1. Validates all 5 Pilot function-tool parameter schemas (`act`, `locate`, `note`, `report_issue`, `finish`) and the `EpisodeQAReview` JSON schema: asserts max nesting depth $\le 10$, zero recursive `$ref` cycles, `additionalProperties == False` on every object, every property listed in `required`, and tool names matching `^[a-zA-Z0-9_.-]+$` with $\le 1$ dot \[5\].<br>2. Inspects every `scenarios/*/human_reference.json` file and asserts that `human_completion_rate` and `median_human_tics` are `null` unless backed by existing `.lmp` and `manifest.json` files in `human_runs_dir` (enforcing Rule 4 against invented baseline numbers). | M1, M4 |
| `tests/test_responses_replay.py` | Unit-tests `MetaClient` multi-turn tool loop formatting: verifies `include=["reasoning.encrypted_content"]` is sent on stateless tool loops, `reasoning` items are replayed with `summary` and `encrypted_content` immediately before `function_call` items, intermediate assistant messages preserve `phase="commentary"`, and `function_call_output` items carry matching `call_id` (1–64 chars) \[5\]\[6\]. | M1 |
| `tests/test_sam_bearing.py` | **(Section 5.2 dev-only calibration test)**<br>1. Loads 10 recorded fixture frames from `my_way_home` and `freedoom2_map01` with ground-truth `(pos_x, pos_y, angle_deg, camera_fov)` and target object `(obj_x, obj_y)` plus `Label` bounding box.<br>2. Computes optical bearing $\hat{\theta} = \arctan\left(\frac{c_x - W/2}{(W/2)/\tan(\text{fov}/2)}\right)$ from box center $c_x$ and asserts $|\hat{\theta} - \theta_{\text{gt}}| < 3.0^\circ$.<br>3. Feeds recorded `sam-3.1` wire lines (`<0f>0<|box;x1=...;c=0.96|><|mask;...;data=...,!...|>`) through `meta_sam_parser` (`>=0.0.6`), decodes `one_bit` masks via `decode_mask_to_raster`, and verifies box, confidence, area fraction, and vertical band (`above`, `level`, `below`) \[7\]\[8\]. | M2 |
| `tests/test_determinism_and_replay.py` | Initializes `DoomEnv` on `my_way_home` with `seed=101`, executes a fixed 15-primitive trace twice, asserts identical final `(pos_x, pos_y, angle_deg, reward_total)` and identical `.lmp` file size, then runs `replay_episode` at `1280x720` with `advance_action()` and asserts identical final tic/reward and a readable MP4 file \[14\]\[19\]. | M1 |
| `tests/test_plan_renderer.py` | Renders `plan_l3.png` from `my_way_home` and `freedoom2_map01` sector fixtures; asserts output is a valid `640x480` RGB PNG with non-empty wall (`#F0F4F8`), spawn (`#2979FF`), and goal (`#00E676`) pixels, and confirms `plan_renderer` is called only at `tic == 1`. | M2 |
| `tests/test_metrics_and_verifier.py` | Runs `eval/metrics.py` and `qa/verify.py` on synthetic and recorded `telemetry.jsonl` fixtures covering all 9 verifiable issue categories; verifies that Stage 1 assigns `behavior_confirmed` vs. `behavior_contradicted` and Stage 2 distinguishes `AGENT_CONTROL_MISALIGNMENT` (pressing `use` $> 64$ units from a door) from `CONFIRMED_INJECTED_FAULT` (`probe_broken_door`) and `CANDIDATE_NAVIGATION_FRICTION_OR_INTENTIONAL_MAZE` (dead-end occupancy). | M3, M4 |

## 12. Package layout, CLI, on-disk artifacts, and milestones (M0–M5)

### 12.1 Repository structure

```text
generalist-game-qa/
├── pyproject.toml                 # Python >=3.11; deps: vizdoom==1.3.2, openai>=1.60, meta-sam-parser>=0.1.0,
│                                  # pillow, numpy, imageio[ffmpeg], pyyaml, pydantic>=2.0, pytest
├── Makefile                       # targets: setup, doctor, spike, test, test-live, demo, sweep-fast, sweep-core
├── README.md                      # Existing README (only placeholders updated in M4)
├── configs/
│   ├── calibration.json           # Generated by M0 spike (FOV, turn deg/tic, forward units/tic)
│   ├── sweep_fast.yaml            # <6h cut-line sweep (15 episodes)
│   ├── sweep_core.yaml            # Full 45-episode L1/L2/L3 sweep (ordinary gameplay)
│   ├── sweep_probes.yaml          # Controlled fault-injection probe sweep (probe_broken_door)
│   └── sweep_carryover.yaml       # RQ3 carry-over feedback sweep
├── prompts/
│   ├── system_v1.md               # Stable system prompt prefix + tool usage rules
│   ├── level1_task.md             # Pinned verbatim L1 task statement (section 5.3)
│   └── reviewer_v1.md             # Post-episode Reviewer system prompt
├── scenarios/
│   ├── registry.yaml              # Scenario definitions, budgets, and is_fault_probe flags
│   ├── my_way_home/
│   │   ├── briefing_l2.md
│   │   ├── annotations_l3.json
│   │   └── human_reference.json
│   ├── health_gathering_supreme/
│   │   ├── briefing_l2.md
│   │   ├── annotations_l3.json
│   │   └── human_reference.json
│   └── freedoom2_map01/
│       ├── briefing_l2.md
│       ├── annotations_l3.json
│       └── human_reference.json
├── autoqa/
│   ├── __init__.py
│   ├── cli.py                     # Single CLI entry point (`autoqa ...`)
│   ├── config.py                  # Pydantic config models & price table
│   ├── doom/
│   │   ├── types.py               # GameEnv protocol, AgentObservation, ActionFeedback
│   │   ├── env.py                 # DoomEnv wrapper, primitive execution, fault probe hook, .lmp recording
│   │   ├── telemetry.py           # Eval-only TelemetryRecorder & 128-unit grid BFS
│   │   ├── plan_renderer.py       # Static L3 floor plan renderer from state.sectors
│   │   └── replay.py              # .lmp to captioned H.264 MP4 renderer
│   ├── agent/
│   │   ├── client.py              # Meta Model API Responses wrapper, retry, payload sanitizer
│   │   ├── tools.py               # Function tool JSON schemas (act, locate, note, report_issue, finish)
│   │   ├── grounder.py            # SAM 3.1 (`sam-3.1`) caller, meta_sam_parser decoder, bearing math
│   │   ├── memory.py              # Scratchpad (1,500 chars) + rolling 20-step action log
│   │   ├── guards.py              # Budget, repeat/stuck warning, and API error guards
│   │   ├── carryover.py           # Self-observed Attempt 1 -> Attempt 2 brief builder (RQ3)
│   │   └── pilot.py               # Main Pilot decision loop
│   ├── prompts/
│   │   └── builder.py             # Cache-aligned prompt assembly + level firewall assertions
│   ├── eval/
│   │   ├── metrics.py             # Episode and sweep metrics, Wilson/bootstrap CIs, sensitivity classifier
│   │   └── vjepa_analysis.py      # M5 offline V-JEPA 2 embedding novelty & revisit correlation
│   └── qa/
│       ├── schemas.py             # IssueRecord & EpisodeQAReview Pydantic/JSON schemas
│       ├── reviewer.py            # Post-episode Muse Spark video + log QA pass
│       ├── verify.py              # 2-stage trace behavior verifier & causal triage classifier
│       └── report.py              # Self-contained HTML report, clip cutter, and dossier builder
├── scripts/
│   ├── run_m0_spike.py            # M0 verification & calibration script (writes docs/spike-results.md)
│   └── update_readme.py           # Populates only placeholder blocks in README.md
├── docs/
│   ├── spike-results.md           # Measured M0 findings
│   └── progress.md                # Milestone log (updated at end of M0..M5)
└── tests/                         # Full pytest suite (section 11)
```

### 12.2 CLI commands (`autoqa/cli.py`)

```bash
# 1. Pre-flight check & M0 calibration spike
autoqa doctor
autoqa spike --out docs/spike-results.md --calibration configs/calibration.json

# 2. Single episode run (L1, L2, or L3; optional carry-over from prior run)
autoqa run --scenario my_way_home --level 1 --seed 101 \
  --model muse-spark-1.3 --effort low --max-decisions 60 --max-usd 0.25 \
  [--carry-over runs/20261104T120000_my_way_home_L1_s101]

# 3. Replay a recorded .lmp into a captioned MP4 (full episode or tic slice)
autoqa replay runs/<run_id> [--from-tic 100 --to-tic 310] [--size 1280x720] [--captions]

# 4. Run post-episode Reviewer, deterministic trace verifier, and HTML report on one run
autoqa review runs/<run_id> [--model muse-spark-1.3] [--effort high]
autoqa verify runs/<run_id>
autoqa report runs/<run_id>

# 5. Execute a multi-scenario, multi-level experiment sweep and generate full dossier
autoqa sweep --config configs/sweep_core.yaml --out results/sweep_core
autoqa sweep --config configs/sweep_probes.yaml --out results/sweep_probes
autoqa report results/sweep_core

# 6. Optional: Record a real human reference playtest in ViZDoom SPECTATOR mode
autoqa record-human --scenario freedoom2_map01 --seed 101
```

### 12.3 On-disk artifact layout per run and sweep

Every `autoqa run` creates an immutable directory `runs/<timestamp>_<scenario>_L<level>_s<seed>/`:
- `manifest.json`: Git commit SHA, timestamp, `vizdoom.__version__`, IWAD path and SHA-256, scenario config SHA-256, prompt SHA-256, `level`, `seed`, `model`, `effort`, `is_fault_probe`, `fault_injection`, `budgets`, terminal `outcome`, total wall time, token usage, and `total_usd`. Never contains API keys or HTTP request headers.
- `events.jsonl`: Append-only log of every Pilot decision, tool call, SAM `locate` output, guard note, token usage (`input_tokens`, `cached_tokens`, `output_tokens`, `reasoning_tokens`), and API latency in ms.
- `telemetry.jsonl`: Ground-truth engine telemetry (section 9.1).
- `episode.lmp`: Native ViZDoom demo recording flushed via trailing `new_episode()` \[19\].
- `frames/`: `step_0000.jpg` .. `step_NNNN.jpg` plus `plan_l3.png` (when `level == 3`).
- `metrics.json`: Computed episode metrics (section 9.2).
- `issues.json`: Validated `IssueRecord` array after `review` and `verify` (section 10.1).
- `clips/`: Short H.264 MP4 evidence clips for trace-confirmed issues.
- `report.html`: Standalone single-run HTML QA report.

Every `autoqa sweep` creates `results/<sweep_id>/`:
- `sweep_manifest.json`: List of constituent `run_id` directories, sweep config hash, and completion status.
- `summary.csv` and `summary.json`: Aggregated metrics per `(scenario, level)`, paired L1/L2/L3 deltas, information-sensitivity classifications, and behavior-confirmation rates.
- `issues_all.json`: All deduplicated issues across the sweep.
- `heatmaps/`: Top-down trajectory and visit-density PNGs per `(scenario, level)`.
- `report.html` and `dossier.md`: Complete multi-scenario experiment report and judge-ready QA dossier.

### 12.4 Milestones M0–M5, timebox ceilings, and de-scoping triggers

Because M0–M4 span API integration, ViZDoom control, SAM 3.1 segmentation, three information conditions, multi-seed sweeps, trace verification, and HTML reporting, the hour figures below are **hard timebox ceilings for cutting scope, not completion forecasts**. Whenever a milestone hits its timebox ceiling, execute its **De-scope trigger** immediately so `main` stays runnable and shippable.

#### M0 — API & ViZDoom Spike and Calibration (Timebox ceiling: 1.5 h)
- **Scope**: Implement `scripts/run_m0_spike.py` and `autoqa doctor` to execute all 10 verification items in section 2.5. Record measured numbers in `docs/spike-results.md` and write `configs/calibration.json`.
- **De-scope trigger at 1.5 h**: If `TURN_LEFT_RIGHT_DELTA` calibration or `meta_sam_parser` wire decoding hits edge cases, use binary `TURN_LEFT` / `TURN_RIGHT` with calibrated tics-per-90-degrees for M1 and defer SAM 3.1 parser debugging to M2.
- **Acceptance criteria**:
  1. `GET /v1/models` confirms access to `muse-spark-1.3` and `sam-3.1`; rate-limit headers (`x-ratelimit-*`) are logged \[1\]\[2\].
  2. One Responses API tool-call round trip with a 640x480 Doom JPEG and `include=["reasoning.encrypted_content"]` succeeds \[3\]\[6\].
  3. One `sam-3.1` streaming request with `metadata={"mask_encoding": "one_bit", "include_confidence": "true"}` is decoded by `meta_sam_parser` \[7\]\[8\].
  4. Turn calibration, forward speed, `CAMERA_FOV`, and `my_way_home` vest label name (`GreenArmor`) are measured and saved to `configs/calibration.json`.
  5. `make test` passes initial smoke tests; commit + `docs/progress.md` entry added.

#### M1 — End-to-End Level 1 Pilot Loop, Telemetry, and Replay (Timebox ceiling: 2.5 h)
- **Scope**: Implement `DoomEnv`, `TelemetryRecorder`, `MetaClient`, `Pilot` loop (with `act`, `note`, `report_issue`, `finish`), `guards.py`, `.lmp` recording, and `autoqa replay`.
- **De-scope trigger at 2.5 h**: Skip burned text captions in `autoqa replay` (emit plain H.264 MP4 from `.lmp` frames) and move directly to M2.
- **Acceptance criteria**:
  1. `autoqa run --scenario my_way_home --level 1 --seed 101` runs end-to-end with real `muse-spark-1.3` tool calls and zero scripted fallback actions.
  2. `autoqa replay runs/<id> --size 1280x720` produces a valid H.264 MP4 whose final tic and reward match `telemetry.jsonl`.
  3. `tests/test_info_barrier.py`, `tests/test_no_scripted_policy.py`, `tests/test_responses_replay.py`, and `tests/test_determinism_and_replay.py` are green.

#### M2 — SAM 3.1 Grounder (`locate`) and Information Levels L1, L2, L3 (Timebox ceiling: 3.0 h)
- **Scope**: Implement `autoqa/agent/grounder.py` (`sam-3.1` + `meta_sam_parser` + optical bearing calculation), `scenarios/*/briefing_l2.md`, `autoqa/doom/plan_renderer.py` (static L3 floor plan from `state.sectors`), and `prompts/builder.py`.
- **De-scope trigger at 3.0 h**: If custom `state.sectors` polygon rendering takes $> 45$ min, use the Section 6.3 fallback (capture ViZDoom's native `state.automap_buffer` once at `tic=1` with `AutomapMode.WHOLE` and immediately disable `automap_buffer`), or ship L1 + L2 first under the `<6h` cut line (section 0.1).
- **Acceptance criteria**:
  1. `tests/test_sam_bearing.py` confirms `< 3.0°` bearing MAE on fixture frames and decodes `sam-3.1` masks and confidences.
  2. `tests/test_plan_renderer.py` and `tests/test_info_barrier.py` pass for L1, L2, and L3.
  3. One real episode each of L1, L2, and L3 completes on `my_way_home` (`seed=101`), confirming identical initial spawn state across all three levels and non-zero `cached_tokens` after turn 1. (`M0 + M1 + M2` is a complete, submittable baseline).

#### M3 — Multi-Scenario Experimental Sweep & Metrics Engine (Timebox ceiling: 2.5 h)
- **Scope**: Implement `autoqa/eval/metrics.py`, `autoqa sweep`, trajectory/coverage heatmaps, and information-sensitivity classification (section 8.4). Execute `sweep_core.yaml` (or `sweep_fast.yaml` if $< 6$ h remain).
- **De-scope trigger at 2.5 h**: Switch immediately from `sweep_core.yaml` (45 episodes) to `sweep_fast.yaml` (15 episodes: 3 seeds $\times$ `my_way_home` L1/L2/L3 + `freedoom2_map01` L1/L2) and reduce `max_decisions` on `freedoom2_map01` from 100 to 60 so wall-clock sweep time stays under 60 minutes.
- **Acceptance criteria**:
  1. Paired runs across information levels complete on at least `my_way_home` and `freedoom2_map01`.
  2. `results/<sweep_id>/summary.json` and `summary.csv` report completion rate, `progress_fraction`, `spl`, `coverage_fraction`, `revisit_rate`, `collision_rate`, `locate_hit_rate`, `use_precision`, and `cost_usd` with paired deltas $\Delta(\text{L2}-\text{L1})$ and $\Delta(\text{L3}-\text{L2})$.
  3. Every evaluated scenario receives a deterministic information-sensitivity classification (`SELF_EVIDENT_TO_AGENT`, `DISCOVERABILITY_SENSITIVE`, `NAVIGATION_SENSITIVE`, `PERSISTENT_FAILURE_L1_TO_L3`, or `NEGATIVE_MAP_TRANSFER`).

#### M4 — Post-Episode Reviewer, Trace Verifier, Carry-Over (RQ3), and Reports (Timebox ceiling: 3.0 h)
- **Scope**: Implement `qa/verify.py`, `qa/report.py` (`report.html` + `dossier.md` + clip cutting), `agent/carryover.py` (section 8.5), `qa/reviewer.py`, fault-probe validation (`probe_broken_door`), and `scripts/update_readme.py`.
- **Implementation order & de-scope trigger within M4**: Build `qa/verify.py` (operating on inline Pilot `report_issue` calls) and `qa/report.py` **first**, before `qa/reviewer.py`. If $< 90$ minutes remain after `verify.py` and `report.html` work on inline issues, skip the post-episode video Reviewer upload (`--skip-reviewer-video`) and run carry-over (`sweep_carryover.yaml`) on 3 seeds of `my_way_home` L2 only.
- **Acceptance criteria**:
  1. On `probe_broken_door` (kept strictly separate from ordinary gameplay statistics), the system reports `INTERACTION_NO_EFFECT` / `BLOCKED_PATH` / `SOFTLOCK_SUSPECTED` and `qa/verify.py` marks `trace_verification_status == "behavior_confirmed"` and `causal_hypothesis == "CONFIRMED_INJECTED_FAULT"`.
  2. On ordinary gameplay runs (`my_way_home`, `freedoom2_map01`), `qa/verify.py` separates `behavior_confirmed` vs. `behavior_contradicted` and assigns non-overclaiming `causal_hypothesis` labels (`AGENT_CONTROL_MISALIGNMENT`, `CANDIDATE_SIGNPOSTING_OR_CONVENTION_GAP`, `CANDIDATE_NAVIGATION_FRICTION_OR_INTENTIONAL_MAZE`, or `CANDIDATE_PROGRESSION_BLOCKER_NEEDS_HUMAN_CHECK`).
  3. Carry-over experiment (`sweep_carryover.yaml`) completes Attempt 1 and Attempt 2 pairs and logs paired deltas for RQ3.
  4. `results/<sweep_id>/report.html` renders all tables, trajectory heatmaps, SAM-annotated keyframes, embedded MP4 clips, and the RQ5 human-reference section (with unmeasured human stats explicitly shown as `null` / `not run`).
  5. `scripts/update_readme.py` populates the four placeholder sections in `README.md` from real `results/` files without modifying existing prose.

#### M5 — Optional Extensions (Execute strictly in order only if $> 12$ h remain)
1. **M5.1 Offline V-JEPA 2 Embedding Analysis (`autoqa/eval/vjepa_analysis.py`)**:
   - Load `facebook/vjepa2-vitl-fpc64-256` (or `vjepa2_1_vit_base_384`) locally via Hugging Face `transformers` / `torch.hub` \[12\]\[13\].
   - Extract L2-normalized spatiotemporal embeddings for sliding 16-frame windows (stride 8 tics) across recorded episodes.
   - Define **embedding revisit score** $R_{\text{vjepa}}(w) = \max_{u < w - 3} \cos(\mathbf{z}_w, \mathbf{z}_u)$ and **embedding novelty** $N_{\text{vjepa}}(w) = 1 - R_{\text{vjepa}}(w)$.
   - Compute Spearman rank correlation $\rho$ between episode-mean $R_{\text{vjepa}}$ and ground-truth 128-unit grid `revisit_rate` from `telemetry.jsonl`, demonstrating how V-JEPA 2 estimates exploration stagnation in external game engines that lack coordinate telemetry.
2. **M5.2 Reviewer Model Ablation (`muse-wonder-1.0` vs. `muse-spark-1.3`)**:
   - Run `autoqa review` with `model="muse-wonder-1.0"` (`effort="high"`, Standard mode) on 6 recorded episodes (`my_way_home`, `freedoom2_map01`, `probe_broken_door`) and compare issue count, `behavior_confirmation_rate`, latency, and USD cost against `muse-spark-1.3` \[1\]\[2\].
3. **M5.3 Grounder Ablation (`locate` enabled vs. disabled)**:
   - Run 5 paired seeds on `my_way_home` (L2) and `freedoom2_map01` (L2) with `locate` tool removed from `TOOLS`, comparing `use_precision`, doorway collision rate, and completion rate against the standard Pilot + SAM 3.1 configuration.

## 13. API validation, pre-flight checks, and contingency plan

### 13.1 Pre-flight verification (`autoqa doctor`)

Before starting M0 or any sweep, `autoqa doctor` executes a 6-step diagnostic in under 15 seconds:

1. **Environment & secrets**: Confirms Python $\ge 3.11$, `MODEL_API_KEY` is non-empty, `.env` is listed in `.gitignore`, and `ffmpeg` is available via `imageio_ffmpeg.get_ffmpeg_exe()`.
2. **Model catalog & rate limits**: Calls `GET https://api.meta.ai/v1/models` and verifies `muse-spark-1.3` and `sam-3.1` are present in `data[].id` \[1\]. Sends a minimal 16-token `POST /v1/responses` call and logs `x-ratelimit-limit-requests`, `x-ratelimit-remaining-requests`, `x-ratelimit-limit-tokens`, and `x-ratelimit-remaining-tokens` \[2\].
3. **Token counter check**: Calls `POST https://api.meta.ai/v1/responses/input_tokens` with a 640x480 test JPEG (`quality=85`) and records the exact per-frame input token count \[3\].
4. **Stateless reasoning + tool call check**: Calls `POST /v1/responses` on `muse-spark-1.3` with `reasoning={"effort": "low"}`, `include=["reasoning.encrypted_content"]`, and the `act` tool schema; verifies a `function_call` item and an encrypted `reasoning` item are returned and can be replayed cleanly in turn 2 \[5\]\[6\].
5. **SAM 3.1 streaming + parser check**: Streams a `POST /v1/responses` call on `sam-3.1` (`stream=True`, `metadata={"mask_encoding": "one_bit", "include_confidence": "true"}`) and verifies `meta_sam_parser.parse_image_stream` returns a completed `ImageSegmentationResult` with zero fatal errors \[7\]\[8\].
6. **ViZDoom headless & IWAD check**: Initializes `DoomGame` with `window_visible=False` on `my_way_home.cfg` and `freedoom2.cfg`, steps 5 tics, records a `.lmp`, replays it, and logs the SHA-256 of `freedoom2.wad` \[14\]\[17\].

### 13.2 Token and USD budget math

For a typical 50-decision episode at `640x480` (with 1 current frame + 2 thumbnails $\approx 1,800$ image tokens + $1,200$ cached prefix tokens + $600$ dynamic history tokens + $450$ output/reasoning tokens per decision, plus 8 `sam-3.1` `locate` calls and 1 Reviewer pass) \[2\]\[3\]:

| Cost component per 50-decision episode | Standard (`muse-spark-1.3`) | Contributor (`muse-spark-1.3-contributor`) |
| --- | --- | --- |
| Uncached input ($\approx 120\text{k}$ tokens) | $\$0.150$ | $\$0.012$ |
| Cached input ($\approx 60\text{k}$ tokens) | $\$0.009$ | $\$0.00012$ |
| Output + reasoning ($\approx 25\text{k}$ tokens) | $\$0.106$ | $\$0.005$ |
| SAM 3.1 Grounder ($\approx 8$ images @ $\$2.50 / 1\text{k}$) | $\$0.020$ | $\$0.020$ |
| Post-episode Reviewer (1 video + 4k output tokens) | $\$0.035$ | $\$0.003$ |
| **Estimated total per episode** | **$\approx \$0.32$** | **$\approx \$0.04$** |
| **Full 45-episode Core Sweep + 9 Fault Probes (54 runs)** | **$\approx \$17.28$** | **$\approx \$2.16$** |

Both Standard ($\approx \$17.30$) and Contributor ($\approx \$2.20$) fit comfortably inside the hackathon's $\$150$ credit grant \[21\]. Set per-episode `max_usd: 0.60` (Standard) or `0.15` (Contributor) in `registry.yaml` so a runaway loop can never exhaust team credits.

### 13.3 Contingency matrix

| Failure / constraint observed in M0 | Immediate fallback action |
| --- | --- |
| Team key rate-limited below 150 RPM (e.g., 60 RPM free tier or Contributor mode) | Enable token-bucket pacer in `MetaClient` reading `x-ratelimit-remaining-requests` and sleeping to maintain $\le 80\%$ of the reported RPM window \[2\]. Run sweeps sequentially (`workers=1`). |
| Standard mode credits running low | Set `model: muse-spark-1.3-contributor` in config (10$\times$–20$\times$ cheaper; ensure `reasoning.effort` is never set to `"max"`, which is unsupported on Contributor variants \[1\]\[6\]). |
| `POST /v1/files` video upload slow or rejected during Reviewer pass | Pass `--reviewer-mode keyframes` to send a 24-frame annotated image strip via `input_image` instead of `input_file` MP4 \[3\]. |
| `freedoom1.wad` absent from `vizdoom.install_path` | Use `freedoom2.cfg` (`MAP01`, bundled with ViZDoom in `freedoom2.wad`) as the primary full-level scenario \[17\]. |
| Regional or account block on `https://api.meta.ai/v1` | Point `AUTOQA_BASE_URL` at a self-hosted `vLLM` / `SGLang` / `llama.cpp` server running open-weight `Muse Glimmer` (`meta-models/Muse-Glimmer-30B`, Apache-2.0) or `Muse Spark Open`, and load `facebook/sam3.1` weights locally \[1\]\[22\]. |

## 14. Sources and references

1. **Meta Model API — Models Overview (`muse-spark-1.3`, `muse-wonder-1.0`, `sam-3.1`, `Muse Glimmer`, `Muse Spark Open`)**: [https://dev.meta.ai/docs/models](https://dev.meta.ai/docs/models)
2. **Meta Model API — Pricing, Rate Limits, and Prompt Caching**: [https://dev.meta.ai/docs/pricing-rate-limits](https://dev.meta.ai/docs/pricing-rate-limits) and [https://dev.meta.ai/docs/prompt-caching](https://dev.meta.ai/docs/prompt-caching)
3. **Meta Model API — Image Understanding, Perception Grounding, and Quickstart**: [https://dev.meta.ai/docs/image-understanding](https://dev.meta.ai/docs/image-understanding) and [https://dev.meta.ai/docs/quickstart](https://dev.meta.ai/docs/quickstart)
4. **Meta Model API — Video and Audio Understanding & File Handling**: [https://dev.meta.ai/docs/video-understanding](https://dev.meta.ai/docs/video-understanding) and [https://dev.meta.ai/docs/file-handling](https://dev.meta.ai/docs/file-handling)
5. **Meta Model API — Structured Output (`text.format`) and Tool Calling (`tools`)**: [https://dev.meta.ai/docs/structured-output](https://dev.meta.ai/docs/structured-output) and [https://dev.meta.ai/docs/tool-calling](https://dev.meta.ai/docs/tool-calling)
6. **Meta Model API — Reasoning (`reasoning.effort`, Encrypted Replay) and Responses API Protocol**: [https://dev.meta.ai/docs/reasoning](https://dev.meta.ai/docs/reasoning) and [https://dev.meta.ai/docs/protocols/responses](https://dev.meta.ai/docs/protocols/responses)
7. **Meta Model API — Segment Anything Model (SAM 3.1) Overview and Segmenting with Prompts**: [https://dev.meta.ai/docs/sam/overview](https://dev.meta.ai/docs/sam/overview) and [https://dev.meta.ai/docs/sam/segmenting](https://dev.meta.ai/docs/sam/segmenting)
8. **Meta Model API — SAM 3.1 Client Libraries (`meta-sam-parser`), Output Grammar, and Cookbook**: [https://dev.meta.ai/docs/sam/client-libraries](https://dev.meta.ai/docs/sam/client-libraries), [https://dev.meta.ai/docs/sam/reading-segmentation](https://dev.meta.ai/docs/sam/reading-segmentation), and [https://dev.meta.ai/docs/cookbook/sam-api-basics](https://dev.meta.ai/docs/cookbook/sam-api-basics)
9. **Meta Model API Cookbook — Basic Agent Loop and Interleaved Reasoning & Tool Use**: [https://dev.meta.ai/docs/cookbook/basic-agent-loop](https://dev.meta.ai/docs/cookbook/basic-agent-loop) and [https://dev.meta.ai/docs/cookbook/interleaved-reasoning-tool-use](https://dev.meta.ai/docs/cookbook/interleaved-reasoning-tool-use)
10. **Meta Model API Legal — Model Designation and Geographic Use Policy & Supported Countries**: [https://dev.meta.ai/legal/geographic-use-policy](https://dev.meta.ai/legal/geographic-use-policy) and [https://dev.meta.ai/help/accounts-and-login/supported-countries](https://dev.meta.ai/help/accounts-and-login/supported-countries)
11. **Meta AI Developer Blog — Build with Muse Spark, Now Available on Meta Model API**: [https://dev.meta.ai/resources/blog/build-with-muse-spark](https://dev.meta.ai/resources/blog/build-with-muse-spark)
12. **Meta FAIR — V-JEPA 2: Self-Supervised Learning from Video (Repository & Code)**: [https://github.com/facebookresearch/vjepa2](https://github.com/facebookresearch/vjepa2)
13. **Meta FAIR / Hugging Face — V-JEPA 2 and V-JEPA 2.1 Checkpoints**: [https://huggingface.co/facebook/vjepa2-vitg-fpc64-384](https://huggingface.co/facebook/vjepa2-vitg-fpc64-384) and [https://github.com/facebookresearch/vjepa2/issues/137](https://github.com/facebookresearch/vjepa2/issues/137)
14. **ViZDoom Documentation — `DoomGame` Python API Reference**: [https://vizdoom.farama.org/api/python/doom_game/](https://vizdoom.farama.org/api/python/doom_game/)
15. **ViZDoom Documentation — `GameState`, `Label`, `Object`, `Line`, and `Sector` Python API Reference**: [https://vizdoom.farama.org/api/python/game_state/](https://vizdoom.farama.org/api/python/game_state/)
16. **ViZDoom Documentation — `Enums` (`Mode`, `ScreenFormat`, `ScreenResolution`, `AutomapMode`, `GameVariable`, `Button`)**: [https://vizdoom.farama.org/api/python/enums/](https://vizdoom.farama.org/api/python/enums/)
17. **ViZDoom Documentation — Playing Original Doom & Freedoom Levels and SLADE WAD Guide**: [https://vizdoom.farama.org/environments/original_doom_levels/](https://vizdoom.farama.org/environments/original_doom_levels/) and [https://vizdoom.farama.org/environments/slade_config/](https://vizdoom.farama.org/environments/slade_config/)
18. **ViZDoom Documentation — Default Scenarios (`my_way_home`, `deadly_corridor`, `health_gathering_supreme`)**: [https://vizdoom.farama.org/environments/default/](https://vizdoom.farama.org/environments/default/)
19. **ViZDoom Repository — Python Examples (`record_episodes.py`, `objects_and_sectors.py`, `delta_buttons.py`)**: [https://github.com/mwydmuch/ViZDoom/blob/master/examples/python/README.md](https://github.com/mwydmuch/ViZDoom/blob/master/examples/python/README.md)
20. **ViZDoom Documentation — Release Notes (ViZDoom 1.3.2)**: [https://vizdoom.farama.org/release_notes/index.html](https://vizdoom.farama.org/release_notes/index.html)
21. **Meta Global AI Developer Hackathon — Official Portal and Schedule Listing**: [https://dev.meta.ai/events/global-hackathon](https://dev.meta.ai/events/global-hackathon) and [https://eventopia.in/event/meta-global-ai-developer-hackathon-2026-10-26-qvy](https://eventopia.in/event/meta-global-ai-developer-hackathon-2026-10-26-qvy)
22. **Meta AI — Open-Weight Models: Muse Glimmer (30B) and Muse Spark Open (816B MoE)**: [https://dev.meta.ai/docs/open-weight-models](https://dev.meta.ai/docs/open-weight-models), [https://dev.meta.ai/docs/muse-glimmer](https://dev.meta.ai/docs/muse-glimmer), and [https://huggingface.co/meta-models/Muse-Glimmer-30B](https://huggingface.co/meta-models/Muse-Glimmer-30B)
23. **Gameplay VLM Benchmarks — LMGame-Bench (ICLR 2026) and DoomVLM**: [https://proceedings.iclr.cc/paper_files/paper/2026/hash/83a4ea71b13bc86308a2bd0b5e07fb61-Abstract-Conference.html](https://proceedings.iclr.cc/paper_files/paper/2026/hash/83a4ea71b13bc86308a2bd0b5e07fb61-Abstract-Conference.html) and [https://github.com/Felliks/DoomVLM](https://github.com/Felliks/DoomVLM)
24. **Meta Model API — Token Counting (`POST /v1/responses/input_tokens`) and Error Handling**: [https://dev.meta.ai/docs/token-counting](https://dev.meta.ai/docs/token-counting) and [https://dev.meta.ai/docs/error-handling](https://dev.meta.ai/docs/error-handling)
