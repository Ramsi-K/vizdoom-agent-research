# Auto QA agent for ViZDoom: Meta-powered autonomous playtesting, information-level experiments, and QA reporting

*Project: Generalist Game QA, Meta Global AI Developer Hackathon. Drafted 4 Nov 2026 from current Meta Model API, ViZDoom, and V-JEPA documentation. Bracketed numbers point to the source list in section 14.*

**Implementation review through M4.** M0–M3.5 functionality and M4's opt-in QA/carry-over pipeline are implemented, with partial experimental validation; M5 remains planned. The implementation notes below distinguish available behavior from open design targets, especially SAM semantics, the native initial/terminal clocks, and optional map annotations. See the [M2 navigation/replay report](m2-navigation-review.md), [M3.5 combat audit](m35-results.md), and [M4 validation](m4-results.md) for observed results; this document does not establish reliability or causal effects.

## 4. ViZDoom integration requirements

- **Wrapper** `autoqa/doom/env.py`: `Mode.PLAYER`; `window_visible=False` (works without X); `ScreenResolution.RES_640X480`; `ScreenFormat.RGB24`; HUD on, in-game messages on, weapon on, crosshair on, sound off, console off; `episode_timeout=cfg.max_tics` (CLI default 2,100); `set_seed(seed)` before `init()` and again before the recorded `new_episode()`; `viz_nocheat` off. Because `freedoom2.cfg` enables `automap_buffer` and `audio_buffer` at `320x240` and `my_way_home.cfg` defines only 5 binary buttons, `DoomEnv` must explicitly call `set_available_buttons(CANONICAL_BUTTONS)`, `set_screen_resolution(RES_640X480)`, `set_automap_buffer_enabled(False)`, `set_audio_buffer_enabled(False)`, and `set_depth_buffer_enabled(False)` **after** `load_config()` and before `init()` \[14\]\[17\]\[18\].
- **Buffers**: always read the screen buffer for the agent. Call `set_labels_buffer_enabled(True)`, `set_objects_info_enabled(True)`, and `set_sectors_info_enabled(True)` before `init()` for evaluation telemetry and the static initial-state L3 plan only (actual `my_way_home` startup tic is 10) \[15\]. Depth, automap, and audio stay off.
- **Action primitives** (the only way the agent acts); each maps to button vectors and `make_action(..., tics)`:

| Primitive | Buttons | Amount |
| --- | --- | --- |
| forward, backward | `MOVE_FORWARD`, `MOVE_BACKWARD` | tics, 1 to 35; optional `run` adds `SPEED` |
| strafe\_left, strafe\_right | `MOVE_LEFT`, `MOVE_RIGHT` | tics, 1 to 35 |
| turn\_left, turn\_right | `TURN_LEFT_RIGHT_DELTA` | degrees, 1 to 180; the current implementation applies the requested delta in one tic, with button maximum 0 (no configured clipping), as verified by M0. Multi-tic delta splitting and binary-turn fallback were design contingencies, not active runtime modes \[14\]\[16\] |
| use | `USE` | one tic press |
| attack | `ATTACK` | tics, 1 to 35 |
| wait | none | tics, 1 to 35 |

At most 4 primitives and 70 tics per `act` call. Turn handling must use the same implementation at every level and be logged (requested degrees, tics used).

M3.5 combat profiles opt into additional controls: a per-primitive `fire` flag for simultaneous firing/movement/turning, vertical aim, weapon selection/cycling, jump, crouch, reload and alternate attack. The legacy navigation profiles retain their original controls and action limits. Controls are exposed to Muse, not chosen by a scripted combat policy; availability does not mean each control was used. See the [combat protocol and usage audit](m35-results.md).

- **Observation** `AgentObservation` (frozen dataclass): current frame as JPEG (quality 85), up to two previous frames as 320x240 thumbnails, `frame_change_pct` (mean absolute pixel difference of downscaled grayscale frames between the start and end of the last action, reported as a percentage), step counters, and (L3 only) the frozen initial-state plan image. Nothing else.
- **Telemetry** `autoqa/doom/telemetry.py` (evaluation only, sampled at the actual initial state, after every executed primitive and `act`, and at termination): tic, position x/y/z, angle, health, armor, reward, goal visibility pixels (labels buffer), goal distance, 128-unit grid cell, and the initial evaluation geometry used by L3. Decisions containing no executed action do not emit an action-end telemetry record. Read `POSITION_X`, `POSITION_Y`, `POSITION_Z`, `ANGLE`, `HEALTH`, `ARMOR`, and `CAMERA_FOV` directly in `telemetry.py` via `game.get_game_variable(GameVariable.<NAME>)`, leaving `available_game_variables` empty on `GameState` \[14\]. Never imported by `autoqa.agent` (tested).
- **Recording**: every episode runs with `new_episode(path/episode.lmp)`, followed by `new_episode()` at the end to flush \[19\].
- **Replay** `autoqa replay <run_dir> [--from-tic A --to-tic B] [--size 1280x720] [--captions] [--no-outcome-ending] [--output-name NAME.mp4]`: initializes `DoomGame` with the run manifest's exact scenario, map, and seed, overrides `set_screen_resolution(RES_1280X720)` and `set_render_hud(True)`, calls `game.replay_episode(lmp_path)`, and advances tic-by-tic with `game.advance_action(1, True)` until `game.is_episode_finished()` (never calling `make_action` or sending game commands), writing an mp4 (imageio-ffmpeg, H.264) with optional burned captions from `events.jsonl` aligned by tic \[14\]\[19\].
- **Replay presentation and clocks**: successful full exports append a labeled half-speed recap and held result card from captured footage; `--no-outcome-ending` and sliced clips are gameplay-only. Metadata separates `gameplay_frames` and `ending_frames`. Natural completion can reset the raw clock to zero; replay counts native advances to record a separate elapsed-tic reconstruction without rewriting source telemetry. Existing output files are never overwritten.
- **Determinism**: record `vizdoom.__version__`, IWAD sha256, seed, scenario file hash in the manifest.
- **Budgets** per episode: `max_decisions`, `max_tics`, `max_usd`, wall-clock timeout. Grounding requests are included in usage/cost. USD is checked after returned usage is known, so a final response can cross the threshold; its action is not executed. Failed-request charges may be unknown.

## 5. Agent design

### 5.1 Loop

The original design sketch below retains the formatted loop example. Its helper names describe responsibilities rather than the current Python interfaces; `finished` is initialized and checked explicitly so an agent-declared stop cannot fall through to another decision.

The implemented loop is [`Pilot.run_episode()`](../autoqa/agent/pilot.py). It uses the narrow `PlayerSession`, validates the entire tool batch, handles read-only tools before an `act` or `finish`, and executes actions inside the `act` handler. `MetaClient` sends the model, tools, effort, encrypted-reasoning include, and `store=False`, returning dictionary output items. Grounding requests are charged before any pending action, and L2/L3 actions require a valid model-written NAV annotation citing previously delivered evidence. The production loop also records outcomes, checks visual cycles, and handles API/tool errors; those details are omitted from this sketch.

```python
for decision in range(max_decisions):
    obs = env.observe()  # AgentObservation only
    prompt = prompts.build(level, scenario, memory, obs, guard_notes)
    actions = None
    finished = False
    for call_idx in range(MAX_TOOL_CALLS_PER_DECISION):  # default 4
        resp = meta.responses(
            model,
            prompt,
            tools=TOOLS,
            effort=cfg.effort,
            include=["reasoning.encrypted_content"],
            store=False,
        )
        results = []
        for item in resp.output:
            if item.type != "function_call":
                continue
            tc_args = json.loads(item.arguments)
            if item.name == "locate":
                out = grounder.locate(obs.frame, tc_args)  # SAM 3.1
                results.append(
                    {
                        "type": "function_call_output",
                        "call_id": item.call_id,
                        "output": json.dumps(out),
                    }
                )
            elif item.name == "note":
                out = memory.write(tc_args)
                results.append(
                    {
                        "type": "function_call_output",
                        "call_id": item.call_id,
                        "output": json.dumps(out),
                    }
                )
            elif item.name == "report_issue":
                out = issues.add(tc_args, evidence=obs.refs)
                results.append(
                    {
                        "type": "function_call_output",
                        "call_id": item.call_id,
                        "output": json.dumps(out),
                    }
                )
            elif item.name == "finish":
                finished = True
                end_episode(tc_args)
                break
            elif item.name == "act":
                actions = tc_args
                break
        if actions or finished:
            break
        prompt += (
            resp.output + results
        )  # preserves encrypted reasoning, phase='commentary', and function_call items
    if finished:
        break
    if actions is None:
        log("no_action")
        guards.count_no_action()
        continue
    env.execute(actions)  # the only call site that advances the game
    log_event(...)  # events.jsonl + frame + usage + latency
    if env.episode_finished() or guards.exhausted():
        break
```

### 5.2 Tools (function calling, Responses API)

Define schemas with enums so that decoding is constrained \[5\]. No recursive schemas.

| Tool | Arguments | Behaviour |
| --- | --- | --- |
| `act` | `moves`: 1 to 4 items of `{type, amount}`; `run`: boolean; `intent`: string up to 200 chars | Executes primitives in order. `intent` is logged and used in reports. Ends the decision. |
| `locate` | `concept`: 1 to 4 word noun phrase (door, key, green armor, switch) | Uses only the current JPEG and concept. Default `muse_box_sam` asks Muse for boxes, then streams `sam-3.1` box segmentation; explicit `sam_text` is also supported. Returns up to 5 unverified candidates with confidence, optical bearing (negative left), vertical band, box and area fraction; empty output and `could_not_verify` failures are distinct. Local masks/overlays are not sent into model context. L2/L3 receive citation receipts and reuse exact same-frame/concept results. |
| `note` | `text`; `mode`: append or replace | Writes the existing landmark/outcome scratchpad (1,500 characters, oldest trimmed). L2/L3 also maintain their model-written NAV annotation here; no separate navigation memory is added. |
| `report_issue` | `category`, `severity`, `summary` up to 300 chars, `evidence_steps`: ints (default current), `confidence` 0 to 1 | Adds an unverified inline finding with observed decision IDs (`evidence_steps=[]` uses the current decision). `trace_verification_status` is `not_run`; M4 verification/Reviewer reporting writes separate derived findings without overwriting the inline record. Categories: UNCLEAR\_OBJECTIVE, NAVIGATION\_CONFUSION, DEAD\_END, BLOCKED\_PATH, INTERACTION\_NO\_EFFECT, VISUAL\_AMBIGUITY, MECHANIC\_UNCLEAR, SOFTLOCK\_SUSPECTED, UNREACHABLE\_SUSPECTED, OTHER. Severity: low, medium, high. |
| `finish` | `status`: gave\_up or blocked; `rationale` | Agent-declared stop. Success is detected by the environment, never by the agent. |

SAM uses streaming metadata `{"mask_encoding": "one_bit", "include_confidence": "true"}` and the installed official `parse_responses_stream(..., image_segmentation_format()).final_result()` parser. Box-guided live decoding is verified; independent text localization and candidate identity remain unreliable. The hybrid confidence is the minimum of available Muse/SAM estimates, not a calibrated semantic probability. Every candidate has `identity_verified=false`; `found: false` is not proof of absence.

Bearing from a SAM pixel box (`x1..x2` in `0..W` where `W=640`): `bearing_deg = degrees(atan((cx - W/2) / f))` with `cx = (left + right) / 2` and `f = (W/2) / tan(fov/2)`; take `fov` from the M0 calibration of `CAMERA_FOV`. A dev-only test compares bearings with ground-truth object directions (section 11, M2).

### 5.3 Prompting

- Active shared prompt: `prompts/system_v3.md`; guided additions: `prompts/level2_navigation_v1.md` and `prompts/level3_navigation_v1.md`. `system_v1.md` is the historical M1 version. The combined prefix/briefing hash goes into the manifest. Freeze each protocol version before a comparison and record iterations separately. Do not embed scenario-specific solution routes or tune prompts to outcomes within a frozen comparison.
- Level 1 task statement, pinned verbatim by a test:

> You are an automated QA tester playing an unfamiliar first-person level. You are not told the objective. Work out what the level wants from what you can see, play it as a new player would, try to reach the end of the level, and report anything that confuses you or looks broken.

- Layout for caching: stable prefix first (system prompt, controls, level sections, L2/L3 briefing); dynamic suffix after (scratchpad, recent action log, thumbnails, current frame). Log `response.usage.input_tokens_details.cached_tokens` and `response.usage.output_tokens_details.reasoning_tokens` \[2\]\[6\].
- Frames: current frame full size; previous two as labelled thumbnails (`t-1`, `t-2`). Text history: last 20 decisions, one row each (moves, intent, visible frame change, and uncertain localization receipts); each row is bounded at 1,600 characters.
- Output is tool calls only (`tool_choice="auto"`). If the model answers in plain text, retry once with a reminder; a second miss counts as `no_action`.
- The controls list states primitives and units. It does not say what `use` does to doors or switches; that is a Level 2 mechanic.

### 5.4 Memory

There are two model-visible text stores: the agent-written 1,500-character landmark/outcome scratchpad and the 20-decision action/tool history. The harness also keeps bounded evidence IDs and visual descriptors for citation checks/warnings; these do not compute or supply navigation. Ordinary episodes start fresh. M4's explicit carry-over mode in section 8.5 adds an 800-character brief from the prior attempt's self-observed notes, warnings, actions and outcome, excluding evaluation telemetry and Reviewer advice. Live M4 pairs used extractor v1; v2 is the current default but has only offline validation. See [measured carry-over results](m4-results.md#carry-over-study-rq3).

For L2/L3, the same scratchpad contains a single-line `NAV {...}` annotation: current decision, model-written basis, tentative location/heading, destination, up to three subgoals, grounding references, uncertainty, replanning reason and next visual check. L2 cites actual recent `locate` receipts, including inconclusive results. L3's declared strategic basis is `map`; screenshots guide local movement. An act requires an annotation no more than five decisions old, refreshed on a visual-cycle warning. Grounding receipts must have been delivered on an earlier tool round and be less than 20 decisions old. Missing/invalid annotations yield tool errors, never inserted actions. `navigation_update` and `navigation_action` are logged with `planning_verified=false`: these are model-written intentions, not verified localization, route quality or proof that the map caused success.

### 5.5 Guards (harness-level; logged; never choose actions)

- Budgets: `max_decisions`, `max_tics`, `max_usd` (checked after every response from usage times the price table), wall-clock timeout.
- Repeat detection: if the same primitive tuple is issued three times in a row with `frame_change` below 2 percent, add a text note to the next prompt (`Your last three identical actions changed nothing on screen. Try something different.`) and log a `stuck_warning` event \[9\].
- Visual cycles: compare the top 75% of the current RGB image (excluding the HUD/most weapon motion) at 32×24 resolution with up to 48 prior descriptors. Warn at mean absolute difference ≤0.04 on [0,1] against two views at least six decisions old and three decisions apart, with a four-decision warning cooldown. This emits `visual_cycle_warning` text, acknowledges lookalike places and does not infer coordinates or select a route.
- API errors: the Pilot retries connection/time-out errors, 429 and 5xx with exponential backoff and jitter starting at 500 ms, honouring `Retry-After`; never retry other 4xx \[2\]. After at most five attempts for a request, abort as `invalid_api_error` (or `budget_exhausted` if its deadline was reached). Grounder requests currently make one attempt and return explicit failure. M3/M4 aggregation excludes invalid API outcomes from ordinary gameplay denominators and invalid carry-over pairs; returned usage is retained, with failed-request charges marked unknown.
- Empty/refused/incomplete outputs: a second miss ends the current decision; five consecutive such responses abort. Five decisions without an executed action also abort. Valid notes/lookups can consume a decision without an act; no scripted fallback is supplied.

### 5.6 Model parameters

`muse-spark-1.3`, Responses API, `reasoning.effort` default `low` for the Pilot (CLI alternatives: `minimal`, `medium` and `high`), `max_output_tokens=3000` by default, including reasoning tokens, parallel tool calls allowed, `tool_choice="auto"`, `temperature` is not sent; the provider default is used. The M3.5 Corridor demo used Pilot effort `high` and a 6,000-token ceiling. M4's Reviewer uses `high` with a default 4,000-token ceiling; separate 8,000-token diagnostic variants are documented in its report. These runs do not establish a causal reasoning-effort benefit. Model ID, effort, and variant (Standard or Contributor) are config values recorded in every manifest.

## 6. Information levels and code-level enforcement

### 6.1 Information-level definitions

The harness evaluates the exact same `muse-spark-1.3` Pilot agent, action space, and `sam-3.1` Grounder across three progressively richer information conditions. The current implementation also varies level-specific navigation instructions and the L2/L3 NAV gate, not just the briefing/map. Treat outcomes as comparisons of these bundled protocols; their differences do not by themselves establish an information-only causal effect:

| Dimension | Level 1 — Blind Play | Level 2 — Guided Play | Level 3 — Informed Play |
| --- | --- | --- | --- |
| **Player persona & task** | Pinned L1 task statement (section 5.3): infer objective and mechanics from pixels alone | Explicit scenario objective, win condition, and failure conditions | Explicit objective, win/failure conditions, plus structural level topology |
| **Controls specification** | Primitive names and units (`tics`, `degrees`) only; no mention of what `use` activates | Adds Doom interaction mechanics (`use` on doors, switches, lifts; keycards; hazard floors; pickups) | Same as Level 2 |
| **Visual inputs per step** | Current 640x480 RGB JPEG (`frame_jpeg`) + up to two 320x240 thumbnails (`t-1`, `t-2`) | Same as Level 1 | Same as Level 1 + static 640x480 structural floor plan (`plan_l3.png`) generated once at the actual initial state (`my_way_home`: native tic 10) |
| **Spatial / map context** | None | None | Frozen walls/thresholds, initial spawn/heading, known armor goals and terminal-branch tags; key/Freedoom exit annotations remain open targets |
| **Runtime tools** | `act`, `locate`, `note`, `report_issue`, `finish` | `act`, `locate`, `note`, `report_issue`, `finish` | `act`, `locate`, `note`, `report_issue`, `finish` |
| **Engine telemetry access** | Forbidden (`AgentObservation` only) | Forbidden (`AgentObservation` only) | Forbidden; only the permitted frozen initial map is supplied |
| **Navigation protocol** | Model-chosen actions and existing ledger | Current NAV citing recent grounding evidence; visual reasoning when uncertain | Current map-based NAV with tentative location, route/subgoals and replanning |

### 6.2 Level 2 briefing contract (`scenarios/<id>/briefing_l2.md`)

Level 2 appends a scenario briefing to the shared system-prompt prefix, followed by the level-specific navigation instructions. The briefing and combined prompt are hashed into `manifest.json`. The four-part briefing contract is:

1. **Objective**: Exact condition that ends the episode as a success (for example, *"Navigate the interconnected rooms and walk over the green armor vest (`GreenArmor`) to collect it before the 2,100-tic timer expires"* for `my_way_home`, or *"Reach the exit room, face the exit switch on the wall, and press `use`"* for `freedoom2_map01`).
2. **Core mechanics**:
   - `use` interacts with doors, wall switches, and lift platforms when the player is within approximately 64 map units and facing the surface. Standard doors slide open vertically for several seconds after `use`; switches change texture and trigger a door or lift.
   - Colored doors or bars require picking up the matching colored keycard or skull key first.
   - Green/bright floor slime (nukage/acid) inflicts periodic damage while standing on it; medkits restore health, whereas dark poison vials in hazard scenarios inflict 30 damage on contact.
   - Walking directly over an item (vest, medkit, key, ammo) collects it automatically without pressing `use`.
3. **Failure modes**: Episode timeout (`episode_timeout` tics) or player health reaching 0.
4. **Exclusions**: L2 briefings must never state room colors along the solution path, turn sequences, compass headings, or map coordinates.

### 6.3 Level 3 structural floor plan (`autoqa/doom/plan_renderer.py`)

**Current M2 behavior:** before the first observation, `DoomEnv` renders one 640×480 PNG from the evaluation recorder's initial geometry/object snapshot. It uses the actual initial native tic (10 on `my_way_home`), reserves space for its legend, draws walls/thresholds, spawn/heading, known armor goals, north/scale and topology-derived terminal-branch X tags. The PNG is then frozen. Freedoom's exit is unannotated; key/hazard markers and loading `annotations_l3.json` are not implemented.

The detailed list below is the original **design target**, not a claim that all annotations or the literal tic-1 timing have shipped. These open requirements are retained for comparison with the implementation \[15\]:

1. **Coordinate projection**: Compute the axis-aligned bounding box $(x_{\min}, y_{\min}, x_{\max}, y_{\max})$ across all line vertices in `state.sectors`. Apply a uniform scale factor $s = \min\left(\frac{640 - 2p}{x_{\max} - x_{\min}}, \frac{480 - 2p}{y_{\max} - y_{\min}}\right)$ with padding $p = 32$ px, centering the map on a dark slate canvas (`#12161F`) with Doom $+Y$ (North) pointing up.
2. **Geometry rendering**:
   - Iterate over every `sector` in `state.sectors` and every `line` in `sector.lines` \[15\].
   - If `line.is_blocking` is `True`, draw a 2 px solid white line (`#F0F4F8`).
   - If `line.is_blocking` is `False` and adjacent sectors differ in `floor_height` or `ceiling_height`, or if `ceiling_height == floor_height` (closed door sector), draw a 2 px dashed cyan line (`#00E5FF`) to denote a doorway, step, or threshold.
   - If a sector is tagged as damaging floor in `annotations_l3.json`, fill its polygon with translucent amber (`#FFAB00`, alpha 60).
3. **Static markers and legend**:
   - **Spawn (`S`)**: Draw a filled blue triangle (`#2979FF`, radius 8 px) at initial `(pos_x, pos_y)` oriented along initial `angle_deg` (`tic == 1`), labelled `S (Start)`.
   - **Goal (`G`)**: Draw a green star/circle (`#00E676`, radius 8 px) at the goal object (`GreenArmor` in `state.objects`) or exit switch coordinates from `annotations_l3.json`, labelled `G (Goal)`.
   - **Landmarks**: Render static labels from `annotations_l3.json` (`D1..Dn` for doors, `K` for keys, `X` for known dead-end branches).
   - **Compass & scale bar**: Draw a `N` arrow in the top-right corner and a `256 map units` scale bar in the bottom-left corner.
4. **No live GPS tracking**: `plan_l3.png` is generated **once** at the actual initial state and frozen for the remainder of the episode (the original timing target was `tic == 1`). Updating the player marker on every step would leak ground-truth odometry (`POSITION_X`, `POSITION_Y`, `ANGLE`) into the agent loop and violate Rule 3 (section 0.2). The agent must localize itself on the static floor plan using visual landmarks, just as a human tester reads a paper map.
5. **Unimplemented timebox fallback design**: If custom `state.sectors` polygon rendering takes longer than 45 minutes during M2, capture ViZDoom's native `state.automap_buffer` once at `tic == 1` with `AutomapMode.WHOLE` \[16\], overlay the static `S` and `G` markers, and immediately disable `automap_buffer` for all subsequent tics.

### 6.4 Code-level information firewall

Information-level separation is enforced by four mechanisms that are checked on every `make test` run:

1. **Strict module boundary**:
   - `autoqa/doom/types.py` defines `AgentObservation` (frozen dataclass with `decision_idx: int`, `tics_elapsed: int`, `tics_remaining: int`, `frame_jpeg: bytes`, `prev_frames_jpeg: tuple[bytes, ...]`, `frame_change_pct: float`, `plan_png: bytes | None`, and `frame_sha256: str`).
   - `autoqa/agent/*` and `autoqa/prompts/*` are forbidden from importing `vizdoom`, `autoqa.doom.env`, `autoqa.doom.telemetry`, or `autoqa.eval.*`. They receive a narrow `PlayerSession` protocol exposing only `observe() -> AgentObservation`, `execute(act_args) -> ActionFeedback`, `episode_finished() -> bool`, and `level: Literal[1, 2, 3]`.
2. **Runtime observation gate (`DoomEnv.observe`)**:
   - `observe()` serializes the cached RGB24 screen `(480, 640, 3)` to JPEG bytes, includes the visible frame-change feedback computed by `execute()`, attaches cached `plan_l3.png` bytes if and only if `self.level == 3`, and returns `AgentObservation`. The driver updates that screen cache after actions; no privileged buffers are attached.
   - `TelemetryRecorder.record_step()` (`autoqa/doom/telemetry.py`) writes ground-truth labels, objects, geometry and game-variable measurements to evaluation artifacts and returns no agent observation. The initial snapshot supplies the permitted frozen L3 map. Driver-side outcome/clock checks and offline replay may also read the engine, but their privileged state is not supplied to the Pilot as navigation input.
3. **Prompt construction assertions (`autoqa/prompts/builder.py`)**:
   - When `level == 1`: asserts `briefing_l2 is None` and `obs.plan_png is None`; verifies the exact text of the pinned Level 1 task string from section 5.3.
   - When `level == 2`: asserts `briefing_l2 is not None` and `obs.plan_png is None`.
   - When `level == 3`: asserts `briefing_l2 is not None` and `obs.plan_png is not None`.
4. **Request payload sanitizer (`autoqa/agent/client.py`)**:
   - Before each Pilot request, admits only the exact approved current JPEG, prior thumbnails and (L3 only) frozen PNG bytes, with at most `1 + len(obs.prev_frames_jpeg) + (1 if level == 3 else 0)` image blocks. It rejects arbitrary image substitutions, file/video inputs, and forbidden telemetry keys (`pos_x`, `pos_y`, `labels_buffer`, `object_position`, `sector`) in input text or string message content. There is no implemented `overlay_in_context` setting. Grounding separately validates its current 640×480 JPEG and receives no map or privileged buffers.
