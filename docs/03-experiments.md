# Auto QA agent for ViZDoom: Meta-powered autonomous playtesting, information-level experiments, and QA reporting

*Project: Generalist Game QA, Meta Global AI Developer Hackathon. Drafted 4 Nov 2026 from current Meta Model API, ViZDoom, and V-JEPA documentation. Bracketed numbers point to the source list in section 14.*

This document specifies the scenario suite and evaluation protocol. M2's executed experiments are recorded in the [grounding/baseline report](m2-results.md), [ledger retest](m2-ledger-retest.md), and [navigation review](m2-navigation-review.md). Metrics and configurable sweeps are implemented, but [M3's fast sweep](m3-results.md) stopped after six completed runs. [M3.5](m35-results.md) adds separately reported combat experiments. [M4](m4-results.md) implemented QA verification/reporting and carry-over, with one live fault probe and two valid carry-over pairs. Human recording, measured human baselines and the full experimental matrices remain unrun.

## 7. Scenario suite and controlled QA defect injection

The suite combines bundled ViZDoom benchmarks, Freedoom progression, and a segregated fault-injection probe. Current scenario definitions are in `autoqa/config.py::SCENARIOS`, with briefings under `scenarios/`. `probe_broken_door` is implemented; `scenarios/registry.yaml` remains a design target. M3.5's opt-in combat profiles are documented separately in its report. The matrix below retains the intended scenario budgets; current `autoqa run` uses its CLI limits (defaults: 60 decisions and 2,100 tics), so larger scenario budgets require explicit overrides.

### 7.1 Scenario matrix

| Scenario ID | Pool | Source WAD / Config | Map | Timeout (tics) | Max decisions | Goal condition | Primary research role |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `my_way_home` | Ordinary gameplay | Bundled `my_way_home.cfg` \[18\] | `MAP01` | 2,100 (60 s) | 60 | Collect `GreenArmor` vest (`reward > 0`) | Core L1 vs L2 vs L3 navigation & dead-end recovery benchmark |
| `health_gathering_supreme` | Ordinary gameplay | Bundled `health_gathering_supreme.cfg` \[18\] | `MAP01` | 2,100 (60 s) | 60 | Survive 2,100 tics on acidic floor while collecting medkits and avoiding poison | Mechanic discoverability (L1 vs L2): distinguishing healing vs poison pickups |
| `deadly_corridor_nav` | Ordinary gameplay | Bundled `deadly_corridor.cfg` \[18\] (`doom_skill=1` or `-nomonsters`) | `MAP01` | 2,100 (60 s) | 50 | Reach `GreenArmor` vest at end of corridor (`reward >= 500`) | Straight-line bearing calibration, SAM `locate` range test, and progress baseline |
| `freedoom2_map01` | Ordinary gameplay | Bundled `freedoom2.cfg` + `freedoom2.wad` \[17\] | `MAP01` (*Hydroelectric Plant*) | 4,200 (120 s) | 100 | Activate exit switch (`reward > 0`) with `-nomonsters` | Multi-room level with closed doors (`USE`), switches, height changes, and non-trivial routing |
| `probe_broken_door` | **Controlled fault probe (`is_fault_probe: true`)** | `freedoom2.cfg` + `freedoom2.wad` with environment fault `unresponsive_use_trigger` | `MAP01` | 1,750 (50 s) | 40 | Attempt exit with requested `USE` suppressed; actual exit unreachability unverified | Positive control for `INTERACTION_NO_EFFECT`, `BLOCKED_PATH`, and `SOFTLOCK_SUSPECTED` detection (RQ2, RQ4); excluded from RQ1/RQ3 gameplay averages |

### 7.2 Standardizing buttons, rendering, and controlled environment fault injection

Bundled ViZDoom `.cfg` files define incompatible button subsets (for example, `my_way_home.cfg` omits `MOVE_BACKWARD`, `USE`, `ATTACK`, `SPEED`, and `TURN_LEFT_RIGHT_DELTA`, while `freedoom2.cfg` enables `automap_buffer` and `audio_buffer` at `320x240` \[17\]\[18\]). To prevent scenario configuration differences from confounding the agent loop, `DoomEnv.__init__` enforces a canonical post-load override after `game.load_config(...)` and before `game.init()`:

```python
CANONICAL_BUTTONS = [
    vzd.Button.MOVE_FORWARD,
    vzd.Button.MOVE_BACKWARD,
    vzd.Button.MOVE_LEFT,
    vzd.Button.MOVE_RIGHT,
    vzd.Button.SPEED,
    vzd.Button.USE,
    vzd.Button.ATTACK,
    vzd.Button.TURN_LEFT,
    vzd.Button.TURN_RIGHT,
    vzd.Button.TURN_LEFT_RIGHT_DELTA,
]
```

After `load_config()`, legacy navigation profiles use the following overrides (M3.5 combat profiles opt into additional buttons, without changing legacy controls):
- `game.set_available_buttons(CANONICAL_BUTTONS)`
- `game.set_screen_resolution(vzd.ScreenResolution.RES_640X480)`
- `game.set_screen_format(vzd.ScreenFormat.RGB24)`
- `game.set_render_hud(True)`, `game.set_render_messages(True)`, `game.set_render_weapon(True)`, `game.set_render_crosshair(True)`
- `game.set_depth_buffer_enabled(False)`, `game.set_automap_buffer_enabled(False)`, `game.set_audio_buffer_enabled(False)`
- `game.set_labels_buffer_enabled(True)`, `game.set_objects_info_enabled(True)`, `game.set_sectors_info_enabled(True)`
- `game.set_mode(vzd.Mode.PLAYER)` and `game.set_window_visible(False)`

**Distinction between ordinary gameplay and controlled defect injection (`probe_broken_door`)**:
- In **ordinary gameplay runs** (`is_fault_probe: false`, the default for all benchmarks), `DoomEnv.execute()` is a strict 1-to-1 pass-through: every primitive in the Pilot's `act` tool call is translated directly into its `CANONICAL_BUTTONS` vector and passed to `game.make_action()` without modification (enforcing Section 0.2 Rule 2).
- The **implemented fault-injection probe** (`is_fault_probe: true`) models a build in which requested `USE` fails. `EnvironmentFaultInjector` preserves requested-action records and logs each suppression; it cannot be enabled in ordinary gameplay. Probe manifests record `fault_injection=unresponsive_use_trigger`. The M4 trace-confirmed symptom does not establish valid-door alignment or exit unreachability.

## 8. Experimental methodology and protocol

### 8.1 Core experimental matrix (M3 sweep)

The primary experimental design crosses Information Level $\in \{1, 2, 3\}$ with Scenario and fixed RNG seeds. Fast/core configurations and the runner are implemented; configuration availability does not establish execution. M3 completed only seeds 101 and 102 on `my_way_home` at all three levels. The broader matrix below is retained as the intended design; the fault-probe configuration was reduced for M4 live validation:

| Sweep tier | Scenarios | Information levels | Seeds per cell | Total episodes | Purpose & when to run |
| --- | --- | --- | --- | --- | --- |
| **Core Sweep (`configs/sweep_core.yaml`)** | `my_way_home`, `health_gathering_supreme`, `freedoom2_map01` | L1, L2, L3 | 5 (`101..105`) | 45 | Ordinary gameplay benchmark (RQ1, RQ2, RQ5); run when $\ge 6$ h remain |
| **Fast Cut-Line Sweep (`configs/sweep_fast.yaml`)** | `my_way_home`, `freedoom2_map01` | L1, L2 (plus L3 on `my_way_home`) | 3 (`101..103`) | 15 | Fallback ordinary gameplay sweep when $< 6$ h remain (section 0.1) |
| **Controlled Fault Probe (`configs/sweep_probes.yaml`)** | `probe_broken_door` | L2 | 1 (`101`) | 1 | Executed M4 positive control; kept separate from ordinary gameplay statistics. The original nine-run design was not executed. |

### 8.2 Experimental controls and fairness invariants

1. **Paired seed initialization**: `game.set_seed(s)` is called before `game.init()` and again before the recorded `new_episode()` in a fresh engine for each level. M2's seed-101 runs verified identical initial position and heading across L1/L2/L3. M3 also retained paired spawn/protocol checks for its two completed seeds \[14\].
2. **Fixed settings within each comparison**: M2 uses `system_v3.md`, `muse-spark-1.3`, Pilot effort `low`, at most four primitives/70 tics per `act`, and candidate confidence threshold `0.30`. L2/L3 also use their versioned navigation prompts and NAV validation; L1 does not. Freeze and record these bundled protocols and the grounding mode for future sweeps. `system_v1.md` is historical; M4 uses Reviewer effort `high`; this post-episode role is separate from the Pilot.
3. **No cross-run state**: Every episode starts with an empty scratchpad, empty rolling action log, and a fresh `POST /v1/responses` conversation (except in the explicit carry-over experiment in section 8.5).

### 8.3 Episode outcome taxonomy

At episode termination, the driver combines engine-detected outcomes with the Pilot's stop reason into one primary outcome. The table below defines sweep inclusion rules implemented by M3. Runs can also record `interrupted` or `invalid_runtime_error`; these and unfinalized attempts are excluded from ordinary gameplay aggregates and reported separately:

| Outcome | Condition | Included in gameplay denominator? |
| --- | --- | --- |
| `success` | Goal reached (`reward_total > 0` for exit/vest scenarios, or `tics_elapsed >= episode_timeout and not is_player_dead()` for `health_gathering_supreme`) | Yes |
| `timeout` | `is_episode_timeout_reached()` or `tics_elapsed >= max_tics` without meeting `success` | Yes |
| `max_decisions` | `decision_idx >= max_decisions` before `timeout` or `success` | Yes |
| `player_dead` | `game.is_player_dead()` is `True` | Yes |
| `gave_up` | Pilot invoked `finish(status="gave_up", rationale=...)` | Yes |
| `blocked` | Pilot invoked `finish(status="blocked", rationale=...)` | Yes |
| `budget_exhausted` | Episode USD spend reached `max_usd` or wall-clock limit reached | Yes |
| `invalid_api_error` | Run aborted after 5 consecutive HTTP 429/5xx retries or 5 consecutive empty/refused model responses (section 5.5) | **No** (reported separately in manifest and excluded from success/progress averages) |

### 8.4 Information-sensitivity classification and RQ5 (AI vs. human playability)

Comparing paired outcomes and progress fractions $(P_{\text{L1}}, P_{\text{L2}}, P_{\text{L3}})$ on the same `(scenario, seed)` is intended to assess sensitivity to information. M2 also varies navigation instructions and validation requirements across levels, so it does not isolate an information-only causal effect. The classifier below is implemented, and its labels are **diagnostic hypotheses for developer triage**, not confirmed game defects. Missing required progress measurements yield an unavailable classification, not a fabricated persistent-failure label. All M3 completed triples had unavailable classifications because BFS progress was unresolved:

| Classification | Quantitative rule on paired seed $s$ (or scenario mean) | Diagnostic hypothesis for game developers |
| --- | --- | --- |
| `SELF_EVIDENT_TO_AGENT` | L1, L2, and L3 all succeed, and $P_{\text{L2}} - P_{\text{L1}} < 0.20$ | In-game visual affordances were sufficient for the agent to infer the goal, mechanics, and route without external hints (does not guarantee human readability or enjoyment). |
| `DISCOVERABILITY_SENSITIVE` | L1 fails (or $P_{\text{L2}} - P_{\text{L1}} \ge 0.30$) while L2 and L3 succeed | The agent succeeded once told the objective/mechanics. Suggests inspecting whether objectives, interactables (`USE`), or hazard/pickup distinctions are communicated visually—or whether the game relies on genre conventions a blind agent lacks. |
| `NAVIGATION_SENSITIVE` | L1 and L2 fail (or $P_{\text{L3}} - P_{\text{L2}} \ge 0.30$) while L3 succeeds | Knowing the rules and goal was insufficient without a global floor plan. Points to branching maze topology, dead-end corridors, or low landmark differentiation (which may be either intentional exploration challenge or unintended layout confusion). |
| `PERSISTENT_FAILURE_L1_TO_L3` | L1, L2, and L3 all fail ($P_{\text{L3}} < 0.80$) | Disambiguated by `qa/verify.py`: if `is_fault_probe == True` and the injected fault was triggered, label **`CONFIRMED_INJECTED_FAULT`**; if static sector BFS at `tic=1` proves no topological path exists to the goal, label **`STATIC_TOPOLOGICAL_BLOCKER`**; otherwise label **`UNRESOLVED_AGENT_LIMIT_OR_CANDIDATE_DEFECT`** requiring human inspection of the replay clip. |
| `NEGATIVE_MAP_TRANSFER` | L2 succeeds but L3 fails ($P_{\text{L2}} - P_{\text{L3}} \ge 0.30$) | Supplying the static floor plan degraded agent performance (e.g., visual attention split between map and viewport). |

**Human reference schema (`scenarios/<id>/human_reference.json`, RQ5) — strict anti-fabrication rule**:
To address **RQ5** (*How closely do AI gameplay difficulties correspond to human gameplay difficulties?*) without violating Section 0.2 Rule 4 (*No invented results*), the proposed schema supports two types of entries. Qualitative reference files now exist for `my_way_home` and `freedoom2_map01`; `autoqa record-human` remains unimplemented and measured human playtest baselines have not been collected:
1. **Qualitative design-intent annotations** grounded in published level/scenario documentation (e.g., ViZDoom's documented dead-end corridor in `my_way_home` \[18\], poison vs. medkit pickups in `health_gathering_supreme` \[18\], or standard Doom `USE`-door conventions \[17\]).
2. **Optional measured human playtest statistics** (`human_runs_recorded`, `human_completion_rate`, `median_human_tics`), which **must remain `null` unless actually recorded on disk** via `autoqa record-human --scenario <id>` (which runs ViZDoom in `Mode.SPECTATOR` \[16\] and saves a real `.lmp` and `manifest.json` under `runs/human_<id>/`).

```json
{
  "_comment": "ILLUSTRATIVE SCHEMA ONLY — numeric fields MUST be null unless backed by real SPECTATOR runs in runs/human_*/",
  "scenario_id": "freedoom2_map01",
  "human_runs_dir": null,
  "human_runs_recorded": 0,
  "human_completion_rate": null,
  "median_human_tics": null,
  "documented_design_mechanics": [
    {
      "id": "doom_door_use_convention",
      "category": "MECHANIC_UNCLEAR",
      "description": "Classic Doom/Freedoom doors require pressing USE rather than walking into them [17].",
      "is_intentional_convention": true
    }
  ]
}
```

The M4 report states that no human runs were recorded and distinguishes qualitative mechanics from measured baselines. Reference validation requires matching native-recording/manifest provenance before accepting non-null measured statistics. No human completion rate or median time is claimed here.

### 8.5 Carry-over feedback mode (RQ3)

To investigate **RQ3** (*Does structured feedback improve subsequent attempts?*), M4 implements a separate paired two-attempt protocol through `--carry-over`, `autoqa carryover`, `configs/sweep_carryover.yaml`, and `autoqa/agent/carryover.py`. The landmark/outcome ledger remains within-episode memory. The live study used `configs/sweep_carryover_m4_live_v1.yaml`: three L2 `my_way_home` seeds attempted, two valid pairs, no completion improvement. Extractor v1 was measured; the current v2 default has only offline validation. The broader five-seed matrix below remains unrun:

1. **Attempt 1 (Cold Run)**: Run `(scenario, level, seed)` normally.
2. **Structured Carry-Over Extraction (`autoqa/agent/carryover.py`)**: Build a deterministic JSON/Markdown summary (`carryover_brief.md`, capped at 800 characters) from **only** information available to the agent during Attempt 1:
   - Terminal status seen by the agent (`timeout`, `gave_up`, `blocked`, `player_dead`) and total decisions used.
   - Final content of the agent's own `scratchpad` (`note` tool).
   - List of `stuck_warning` events (`decision_idx`, `moves` that produced `< 2%` frame change, and the agent's stated `intent` at that step).
   - List of self-reported `report_issue` summaries from Attempt 1.
   - **Strict exclusion**: `carryover.py` must never read `telemetry.jsonl`, `metrics.json`, or `qa/reviewer.py` output, ensuring Attempt 2 only receives self-observed memory from Attempt 1.
3. **Attempt 2 (Feedback-Informed Run)**: Re-run the exact same `(scenario, level, seed)` with `carryover_brief.md` injected into the prompt under `## Observations from Your Previous Attempt on This Level`, instructing the Pilot to avoid repeating actions that triggered stuck warnings and to test alternative routes or interactions early.
4. **Evaluation**: Run across 5 seeds on `my_way_home` (L1 and L2) and `freedoom2_map01` (L2). Report paired changes $\Delta \text{completion}$, $\Delta \text{progress\_fraction}$, $\Delta \text{tics\_to\_first\_new\_room}$, and $\Delta \text{stuck\_warning\_count}$.

## 9. Evaluation metrics and telemetry (`autoqa/eval/metrics.py`)

### 9.1 Ground-truth telemetry record (`telemetry.jsonl`)

`TelemetryRecorder` writes an initial record at the actual startup tic (`my_way_home`: 10), a record after each primitive and executed `act`, and a terminal record. The schema example below is illustrative, not a measured episode. Current BFS distances can be unresolved (`null` with a status), goal tracking is limited to observed `GreenArmor` actors, and collision/sector-height changes are proxies. A natural win can reset the raw clock to zero; replay reconstructs elapsed tics separately, preserving source telemetry.

```json
{
  "event": "decision",
  "decision_idx": 7,
  "primitive_idx": 1,
  "tic": 142,
  "pos_x": -384.0,
  "pos_y": 512.0,
  "pos_z": 0.0,
  "angle_deg": 90.0,
  "health": 100.0,
  "armor": 0.0,
  "reward_delta": -0.0035,
  "reward_total": -0.0142,
  "cell_x": -3,
  "cell_y": 4,
  "sector_id": 5,
  "goal_dist_euclidean": 640.2,
  "goal_dist_bfs": 768.0,
  "goal_visible_pixels": 0,
  "goal_bbox": null,
  "displacement_units": 42.6,
  "expected_free_displacement": 45.0,
  "collision_substep": false,
  "door_or_switch_activated": false
}
```

How spatial fields are computed from ViZDoom state \[14\]\[15\]\[16\]:
- `pos_x`, `pos_y`, `pos_z`, `angle_deg`, `health`, `armor`: read via `game.get_game_variable(vzd.GameVariable.POSITION_X)`, etc. (available because `viz_nocheat` is off).
- `cell_x`, `cell_y`: $(\lfloor \text{pos\_x} / 128 \rfloor, \lfloor \text{pos\_y} / 128 \rfloor)$, partitioning the map into $128 \times 128$ map-unit cells (approx. $4\times$ player radius).
- `total_accessible_cells` and `goal_dist_bfs` (design): construct a 128-unit walkable graph from the initial sector snapshot and compute BFS distance from the goal. The current `doom/topology.py` makes this evaluation-only attempt at the actual initial state; distances on the measured `my_way_home` traces remain unresolved on the coarse grid and are reported as `null` with a status. Euclidean fallback without sector data is not a navigable-distance measurement. The metrics engine reports normalized coverage as unavailable when a validated accessible-cell denominator is missing; it is not inferred from visited cells.
- `goal_visible_pixels` and `goal_bbox`: inspect `state.labels`; for any `Label` whose `object_name` matches the scenario's `goal_object_names` (discovered and pinned in M0 item 7, e.g., `"GreenArmor"`), sum `(state.labels_buffer == label.value).sum()` and record `[label.x, label.y, label.width, label.height]` \[15\]\[20\].

### 9.2 Episode and sweep metric definitions (`metrics.json`)

These are M3/M4 metric definitions. `autoqa metrics`, `autoqa analyze` and `autoqa sweep` generate derived episode metrics and aggregates. M2's historical ad hoc trajectory measurements retain their own definitions and must not be silently substituted. M3's BFS progress, SPL and normalized coverage remain unavailable; measured success, exploration proxies and costs are in its report.

| Metric key | Formula / Definition | Answers |
| --- | --- | --- |
| `completed` | `1` if `outcome == "success"` else `0` | RQ1 |
| `progress_fraction` | $\max_{t \in [0, T]} \text{clip}\left(\frac{d_{\text{bfs}}(0) - d_{\text{bfs}}(t)}{d_{\text{bfs}}(0)}, 0, 1\right)$ (`1.0` if `completed == 1`) | RQ1, RQ3 |
| `spl` | $\text{completed} \times \frac{d_{\text{bfs}}(0)}{\max(d_{\text{bfs}}(0), \sum_{t=1}^{T} \|\mathbf{p}_t - \mathbf{p}_{t-1}\|_2)}$ | RQ1 |
| `tics_used` / `decisions_used` | Total game tics elapsed and total Pilot decisions executed | RQ1, RQ3 |
| `unique_cells_visited` | $|\{(\text{cell\_x}_t, \text{cell\_y}_t) : t \in [0, T]\}|$ | RQ1, M5 |
| `coverage_fraction` | `unique_cells_visited / max(1, total_accessible_cells)` | RQ1 |
| `revisit_rate` | $1 - \frac{\text{unique\_cells\_visited}}{\max(1, N_{\text{cell\_entries}})}$, where $N_{\text{cell\_entries}}$ counts transitions into a cell different from the previous step's cell | RQ1, RQ3, M5 |
| `collision_rate` | Fraction of translation primitives (`forward`, `backward`, `strafe_*`) where `displacement_units < 0.20 * expected_free_displacement` | RQ1, RQ2 |
| `stuck_warning_count` | Count of `stuck_warning` events emitted by the repeat guard (section 5.5) | RQ2, RQ3 |
| `locate_calls` / `locate_hit_rate` | Number of calls and candidate-positive fraction at `confidence >= 0.30`; this measures tool returns, not verified identity. Report true localization hits separately using target-matched IoU ≥ 0.5, as in M2. | RQ1, M2 |
| `locate_bearing_mae_deg` | Mean $|\hat{\theta}_{\text{SAM}} - \theta_{\text{gt}}|$ across `locate` calls where a matching `Label` exists in `state.labels` | M2 |
| `use_precision` | `door_or_switch_activated_count / max(1, use_primitives_executed)` | RQ1, RQ2 |
| `issues_reported_inline` | Count of `report_issue` tool calls during play | RQ2 |
| `issues_reviewer_post` | Count of issues returned by `qa/reviewer.py` after play | RQ2 |
| `behavior_confirmed_count` / `behavior_confirmation_rate` | Count of deduplicated issues whose cited behavioral event is confirmed in telemetry (`trace_verification_status == "behavior_confirmed"`), and `behavior_confirmed / max(1, behavior_confirmed + behavior_contradicted)` | RQ4 |
| `cost_usd` / `cache_hit_rate` | Total episode API cost in USD and `cached_input_tokens / max(1, input_tokens)` \[2\] | Engineering efficiency |
