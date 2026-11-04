# Auto QA agent for ViZDoom: Meta-powered autonomous playtesting, information-level experiments, and QA reporting

*Project: Generalist Game QA, Meta Global AI Developer Hackathon. Drafted 4 Nov 2026 from current Meta Model API, ViZDoom, and V-JEPA documentation. Bracketed numbers point to the source list in section 14.*

## 7. Scenario suite and controlled QA defect injection

To answer RQ1–RQ5 with real executions rather than synthetic mocks, the harness ships a 3-tier scenario registry (`scenarios/registry.yaml`) combining bundled ViZDoom benchmarks, full Freedoom map progression, and a strictly segregated fault-injection probe.

### 7.1 Scenario matrix

| Scenario ID | Pool | Source WAD / Config | Map | Timeout (tics) | Max decisions | Goal condition | Primary research role |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `my_way_home` | Ordinary gameplay | Bundled `my_way_home.cfg` \[18\] | `MAP01` | 2,100 (60 s) | 60 | Collect `GreenArmor` vest (`reward > 0`) | Core L1 vs L2 vs L3 navigation & dead-end recovery benchmark |
| `health_gathering_supreme` | Ordinary gameplay | Bundled `health_gathering_supreme.cfg` \[18\] | `MAP01` | 2,100 (60 s) | 60 | Survive 2,100 tics on acidic floor while collecting medkits and avoiding poison | Mechanic discoverability (L1 vs L2): distinguishing healing vs poison pickups |
| `deadly_corridor_nav` | Ordinary gameplay | Bundled `deadly_corridor.cfg` \[18\] (`doom_skill=1` or `-nomonsters`) | `MAP01` | 2,100 (60 s) | 50 | Reach `GreenArmor` vest at end of corridor (`reward >= 500`) | Straight-line bearing calibration, SAM `locate` range test, and progress baseline |
| `freedoom2_map01` | Ordinary gameplay | Bundled `freedoom2.cfg` + `freedoom2.wad` \[17\] | `MAP01` (*Hydroelectric Plant*) | 4,200 (120 s) | 100 | Activate exit switch (`reward > 0`) with `-nomonsters` | Multi-room level with closed doors (`USE`), switches, height changes, and non-trivial routing |
| `probe_broken_door` | **Controlled fault probe (`is_fault_probe: true`)** | `freedoom2.cfg` + `freedoom2.wad` with environment fault `unresponsive_use_trigger` | `MAP01` | 1,750 (50 s) | 40 | Exit unreachable because the environment simulates a broken door/switch trigger | Positive control for `INTERACTION_NO_EFFECT`, `BLOCKED_PATH`, and `SOFTLOCK_SUSPECTED` detection (RQ2, RQ4); excluded from RQ1/RQ3 gameplay averages |

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

After `load_config()`, `DoomEnv` always calls:
- `game.set_available_buttons(CANONICAL_BUTTONS)`
- `game.set_screen_resolution(vzd.ScreenResolution.RES_640X480)`
- `game.set_screen_format(vzd.ScreenFormat.RGB24)`
- `game.set_render_hud(True)`, `game.set_render_messages(True)`, `game.set_render_weapon(True)`, `game.set_render_crosshair(True)`
- `game.set_depth_buffer_enabled(False)`, `game.set_automap_buffer_enabled(False)`, `game.set_audio_buffer_enabled(False)`
- `game.set_labels_buffer_enabled(True)`, `game.set_objects_info_enabled(True)`, `game.set_sectors_info_enabled(True)`
- `game.set_mode(vzd.Mode.PLAYER)` and `game.set_window_visible(False)`

**Distinction between ordinary gameplay and controlled defect injection (`probe_broken_door`)**:
- In **ordinary gameplay runs** (`is_fault_probe: false`, the default for all benchmarks), `DoomEnv.execute()` is a strict 1-to-1 pass-through: every primitive in the Pilot's `act` tool call is translated directly into its `CANONICAL_BUTTONS` vector and passed to `game.make_action()` without modification (enforcing Section 0.2 Rule 2).
- In **controlled fault-injection probes** (`is_fault_probe: true`, used only in `probe_broken_door`), the harness models a broken game build in which the engine's `USE` linedef trigger is non-functional. Rather than a harness guard altering the agent's decision, the scenario initializes `DoomEnv` with an explicit `EnvironmentFaultInjector(fault="unresponsive_use_trigger")` (or binds `USE` to a no-op slot at the environment boundary) and stamps `"is_fault_probe": true, "fault_injection": "unresponsive_use_trigger"` in `manifest.json`. The agent's requested `use` primitive is logged verbatim in `events.jsonl` and `telemetry.jsonl` (`agent_requested_use=True, env_fault_blocked_use=True`). `tests/test_no_scripted_policy.py` verifies that `EnvironmentFaultInjector` raises a `RuntimeError` if instantiated on any scenario where `is_fault_probe` is `False`.

## 8. Experimental methodology and protocol

### 8.1 Core experimental matrix (M3 sweep)

The primary experiment is a paired factorial design crossing Information Level $\in \{1, 2, 3\}$ with Scenario and fixed RNG seeds:

| Sweep tier | Scenarios | Information levels | Seeds per cell | Total episodes | Purpose & when to run |
| --- | --- | --- | --- | --- | --- |
| **Core Sweep (`configs/sweep_core.yaml`)** | `my_way_home`, `health_gathering_supreme`, `freedoom2_map01` | L1, L2, L3 | 5 (`101..105`) | 45 | Ordinary gameplay benchmark (RQ1, RQ2, RQ5); run when $\ge 6$ h remain |
| **Fast Cut-Line Sweep (`configs/sweep_fast.yaml`)** | `my_way_home`, `freedoom2_map01` | L1, L2 (plus L3 on `my_way_home`) | 3 (`101..103`) | 15 | Fallback ordinary gameplay sweep when $< 6$ h remain (section 0.1) |
| **Controlled Fault Probe (`configs/sweep_probes.yaml`)** | `probe_broken_door` | L1, L2, L3 | 3 (`201..203`) | 9 | Positive control for defect detection & verifier sensitivity (RQ2, RQ4); kept separate from Core Sweep statistics |

### 8.2 Experimental controls and fairness invariants

1. **Paired seed initialization**: For a given scenario and seed $s \in \{101..105\}$, `game.set_seed(s)` is called before `game.init()` in a fresh `DoomGame` instance for each of L1, L2, and L3. Because ViZDoom's episode RNG derives spawn room and initial orientation deterministically from `set_seed` on the first episode after `init()`, L1, L2, and L3 start at the exact same `(pos_x, pos_y, angle_deg)` \[14\]. The harness asserts this equality (`< 0.01` map unit tolerance) across paired runs.
2. **Fixed hyperparameters**: Prompt version (`system_v1.md`), model ID (`muse-spark-1.3`), reasoning effort (`low` for Pilot, `high` for Reviewer), action primitive caps (max 4 primitives, max 70 tics per `act`), and `sam-3.1` confidence threshold (`0.30`) are locked across all cells in a sweep.
3. **No cross-run state**: Every episode starts with an empty scratchpad, empty rolling action log, and a fresh `POST /v1/responses` conversation (except in the explicit carry-over experiment in section 8.5).

### 8.3 Episode outcome taxonomy

At episode termination, `DoomEnv` assigns exactly one primary `outcome` string:

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

Comparing paired outcomes and progress fractions $(P_{\text{L1}}, P_{\text{L2}}, P_{\text{L3}})$ on the same `(scenario, seed)` isolates how environmental information affects agent traversal. Because an agent failure never automatically proves a level-design defect, classifications are framed as **diagnostic hypotheses for developer triage**:

| Classification | Quantitative rule on paired seed $s$ (or scenario mean) | Diagnostic hypothesis for game developers |
| --- | --- | --- |
| `SELF_EVIDENT_TO_AGENT` | L1, L2, and L3 all succeed, and $P_{\text{L2}} - P_{\text{L1}} < 0.20$ | In-game visual affordances were sufficient for the agent to infer the goal, mechanics, and route without external hints (does not guarantee human readability or enjoyment). |
| `DISCOVERABILITY_SENSITIVE` | L1 fails (or $P_{\text{L2}} - P_{\text{L1}} \ge 0.30$) while L2 and L3 succeed | The agent succeeded once told the objective/mechanics. Suggests inspecting whether objectives, interactables (`USE`), or hazard/pickup distinctions are communicated visually—or whether the game relies on genre conventions a blind agent lacks. |
| `NAVIGATION_SENSITIVE` | L1 and L2 fail (or $P_{\text{L3}} - P_{\text{L2}} \ge 0.30$) while L3 succeeds | Knowing the rules and goal was insufficient without a global floor plan. Points to branching maze topology, dead-end corridors, or low landmark differentiation (which may be either intentional exploration challenge or unintended layout confusion). |
| `PERSISTENT_FAILURE_L1_TO_L3` | L1, L2, and L3 all fail ($P_{\text{L3}} < 0.80$) | Disambiguated by `qa/verify.py`: if `is_fault_probe == True` and the injected fault was triggered, label **`CONFIRMED_INJECTED_FAULT`**; if static sector BFS at `tic=1` proves no topological path exists to the goal, label **`STATIC_TOPOLOGICAL_BLOCKER`**; otherwise label **`UNRESOLVED_AGENT_LIMIT_OR_CANDIDATE_DEFECT`** requiring human inspection of the replay clip. |
| `NEGATIVE_MAP_TRANSFER` | L2 succeeds but L3 fails ($P_{\text{L2}} - P_{\text{L3}} \ge 0.30$) | Supplying the static floor plan degraded agent performance (e.g., visual attention split between map and viewport). |

**Human reference schema (`scenarios/<id>/human_reference.json`, RQ5) — strict anti-fabrication rule**:
To address **RQ5** (*How closely do AI gameplay difficulties correspond to human gameplay difficulties?*) without violating Section 0.2 Rule 4 (*No invented results*), `human_reference.json` supports two types of entries:
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

If `human_runs_recorded == 0`, `report.html` explicitly prints `"Quantitative human playtest baseline: not recorded (0 human runs); qualitative scenario mechanics shown for triage context only"`. A unit test in `tests/test_schemas_and_tools.py` fails the build if any `human_reference.json` contains non-null `human_completion_rate` or `median_human_tics` without matching `.lmp` and `manifest.json` files in `human_runs_dir`.

### 8.5 Carry-over feedback mode (RQ3)

To answer **RQ3** (*Does structured feedback improve subsequent attempts?*), the harness implements a paired two-attempt protocol (`autoqa run ... --carry-over <attempt1_run_dir>` or `autoqa sweep --config configs/sweep_carryover.yaml`):

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

`TelemetryRecorder` writes one JSON object at `tic=1` (`event="init"`) and one JSON object after every executed primitive (`event="primitive"`) and decision (`event="decision"`):

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
- `total_accessible_cells` and `goal_dist_bfs`: at `tic == 1`, rasterize `state.sectors` onto the 128-unit grid; mark two adjacent cells connected if no line with `is_blocking == True` separates their centers (treating two-sided door lines with `ceiling_height == floor_height` as traversable for global topological distance). Run breadth-first search (BFS) from the goal cell(s) to every reachable cell to obtain `goal_dist_bfs = 128 * bfs_steps`. If the map has no `sectors` data, fall back to `goal_dist_euclidean`.
- `goal_visible_pixels` and `goal_bbox`: inspect `state.labels`; for any `Label` whose `object_name` matches the scenario's `goal_object_names` (discovered and pinned in M0 item 7, e.g., `"GreenArmor"`), sum `(state.labels_buffer == label.value).sum()` and record `[label.x, label.y, label.width, label.height]` \[15\]\[20\].

### 9.2 Episode and sweep metric definitions (`metrics.json`)

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
| `locate_calls` / `locate_hit_rate` | Number of `locate` calls and fraction returning $\ge 1$ object with `confidence >= 0.30` | RQ1, M2 |
| `locate_bearing_mae_deg` | Mean $|\hat{\theta}_{\text{SAM}} - \theta_{\text{gt}}|$ across `locate` calls where a matching `Label` exists in `state.labels` | M2 |
| `use_precision` | `door_or_switch_activated_count / max(1, use_primitives_executed)` | RQ1, RQ2 |
| `issues_reported_inline` | Count of `report_issue` tool calls during play | RQ2 |
| `issues_reviewer_post` | Count of issues returned by `qa/reviewer.py` after play | RQ2 |
| `behavior_confirmed_count` / `behavior_confirmation_rate` | Count of deduplicated issues whose cited behavioral event is confirmed in telemetry (`trace_verification_status == "behavior_confirmed"`), and `behavior_confirmed / max(1, behavior_confirmed + behavior_contradicted)` | RQ4 |
| `cost_usd` / `cache_hit_rate` | Total episode API cost in USD and `cached_input_tokens / max(1, input_tokens)` \[2\] | Engineering efficiency |