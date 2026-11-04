# Auto QA agent for ViZDoom: Meta-powered autonomous playtesting, information-level experiments, and QA reporting

*Project: Generalist Game QA, Meta Global AI Developer Hackathon. Drafted 4 Nov 2026 from current Meta Model API, ViZDoom, and V-JEPA documentation. Bracketed numbers point to the source list in section 14.*

## 4. ViZDoom integration requirements

- **Wrapper** `autoqa/doom/env.py`: `Mode.PLAYER`; `window_visible=False` (works without X); `ScreenResolution.RES_640X480`; `ScreenFormat.RGB24`; HUD on, in-game messages on, weapon on, crosshair on, sound off, console off; `episode_timeout` from the scenario; `set_seed(seed)` before `init()`; `viz_nocheat` off. Because `freedoom2.cfg` enables `automap_buffer` and `audio_buffer` at `320x240` and `my_way_home.cfg` defines only 5 binary buttons, `DoomEnv` must explicitly call `set_available_buttons(CANONICAL_BUTTONS)`, `set_screen_resolution(RES_640X480)`, `set_automap_buffer_enabled(False)`, `set_audio_buffer_enabled(False)`, and `set_depth_buffer_enabled(False)` **after** `load_config()` and before `init()` \[14\]\[17\]\[18\].
- **Buffers**: always read the screen buffer for the agent. Call `set_labels_buffer_enabled(True)`, `set_objects_info_enabled(True)`, and `set_sectors_info_enabled(True)` before `init()` for evaluation telemetry and the static `tic=1` L3 plan only \[15\]. Depth, automap, and audio stay off.
- **Action primitives** (the only way the agent acts); each maps to button vectors and `make_action(..., tics)`:

| Primitive | Buttons | Amount |
| --- | --- | --- |
| forward, backward | `MOVE_FORWARD`, `MOVE_BACKWARD` | tics, 1 to 35; optional `run` adds `SPEED` |
| strafe\_left, strafe\_right | `MOVE_LEFT`, `MOVE_RIGHT` | tics, 1 to 35 |
| turn\_left, turn\_right | `TURN_LEFT_RIGHT_DELTA` | degrees, 1 to 180; because `make_action(action, tics)` repeats the delta value on every tic, execute delta turns in single-tic steps (`make_action([..., chunk_deg], tics=1)`) or divide `chunk_deg = total_deg / tics` across `tics` steps so total rotation equals the requested degrees; fall back to binary `TURN_*` for N tics if M0 shows delta turning is unusable \[14\]\[16\] |
| use | `USE` | one tic press |
| attack | `ATTACK` | tics, 1 to 35 |
| wait | none | tics, 1 to 35 |

At most 4 primitives and 70 tics per `act` call. Turn handling must use the same implementation at every level and be logged (requested degrees, tics used).

- **Observation** `AgentObservation` (frozen dataclass): current frame as JPEG (quality 85), two previous frames as 320x240 thumbnails, `frame_change` (mean absolute pixel difference of downscaled grayscale frames between the start and end of the last action, reported as a percentage), step counters, and (L3 only) the static `tic=1` plan image. Nothing else.
- **Telemetry** `autoqa/doom/telemetry.py` (evaluation only, sampled at `tic=1` and at the end of every primitive and every decision): tic, position x/y/z, angle, health, armor, reward, goal visibility pixels (labels buffer), goal distance, 128-unit grid cell, and for L3 the plan geometry. Read `POSITION_X`, `POSITION_Y`, `POSITION_Z`, `ANGLE`, `HEALTH`, `ARMOR`, and `CAMERA_FOV` directly in `telemetry.py` via `game.get_game_variable(GameVariable.<NAME>)`, leaving `available_game_variables` empty on `GameState` \[14\]. Never imported by `autoqa.agent` (tested).
- **Recording**: every episode runs with `new_episode(path/episode.lmp)`, followed by `new_episode()` at the end to flush \[19\].
- **Replay** `autoqa replay <run_dir> [--from-tic A --to-tic B] [--size 1280x720] [--captions]`: initializes `DoomGame` with the run manifest's exact scenario, map, and seed, overrides `set_screen_resolution(RES_1280X720)` and `set_render_hud(True)`, calls `game.replay_episode(lmp_path)`, and advances tic-by-tic with `game.advance_action(1, True)` until `game.is_episode_finished()` (never calling `make_action` or sending game commands), writing an mp4 (imageio-ffmpeg, H.264) with optional burned captions from `events.jsonl` aligned by tic \[14\]\[19\].
- **Determinism**: record `vizdoom.__version__`, IWAD sha256, seed, scenario file hash in the manifest.
- **Budgets** per episode: `max_decisions`, `max_tics`, `max_usd`, wall-clock timeout.

## 5. Agent design

### 5.1 Loop

```python
for decision in range(max_decisions):
    obs = env.observe()                        # AgentObservation only
    prompt = prompts.build(level, scenario, memory, obs, guard_notes)
    actions = None
    for call_idx in range(MAX_TOOL_CALLS_PER_DECISION):      # default 4
        resp = meta.responses(
            model, prompt, tools=TOOLS, effort=cfg.effort,
            include=["reasoning.encrypted_content"], store=False,
        )
        results = []
        for item in resp.output:
            if item.type != 'function_call':
                continue
            tc_args = json.loads(item.arguments)
            if item.name == 'locate':
                out = grounder.locate(obs.frame, tc_args)    # SAM 3.1
                results.append({"type": "function_call_output", "call_id": item.call_id, "output": json.dumps(out)})
            elif item.name == 'note':
                out = memory.write(tc_args)
                results.append({"type": "function_call_output", "call_id": item.call_id, "output": json.dumps(out)})
            elif item.name == 'report_issue':
                out = issues.add(tc_args, evidence=obs.refs)
                results.append({"type": "function_call_output", "call_id": item.call_id, "output": json.dumps(out)})
            elif item.name == 'finish':
                end_episode(tc_args); break
            elif item.name == 'act':
                actions = tc_args; break
        if actions or finished:
            break
        prompt += resp.output + results        # preserves encrypted reasoning, phase='commentary', and function_call items
    if actions is None:
        log('no_action'); guards.count_no_action(); continue
    env.execute(actions)                       # the only call site that advances the game
    log_event(...)                             # events.jsonl + frame + usage + latency
    if env.episode_finished() or guards.exhausted():
        break
```

### 5.2 Tools (function calling, Responses API)

Define schemas with enums so that decoding is constrained \[5\]. No recursive schemas.

| Tool | Arguments | Behaviour |
| --- | --- | --- |
| `act` | `moves`: 1 to 4 items of `{type, amount}`; `run`: boolean; `intent`: string up to 200 chars | Executes primitives in order. `intent` is logged and used in reports. Ends the decision. |
| `locate` | `concept`: 1 to 4 word noun phrase (door, key, green armor, switch) | Calls `sam-3.1` on the current frame (`stream=True`, `metadata={"mask_encoding": "one_bit", "include_confidence": "true"}`). Returns up to 5 matches with id, confidence, bearing in degrees relative to heading (negative is left), vertical band (above, level, below), pixel box, and area fraction; or `found: false`. Overlay image in context only if `overlay_in_context` is on (default off to save tokens). |
| `note` | `text`; `mode`: append or replace | Writes the agent's scratchpad (capped at 1,500 characters, oldest trimmed). |
| `report_issue` | `category`, `severity`, `summary` up to 300 chars, `evidence_steps`: ints (default current), `confidence` 0 to 1 | Adds a finding with auto-attached frame references. Categories: UNCLEAR\_OBJECTIVE, NAVIGATION\_CONFUSION, DEAD\_END, BLOCKED\_PATH, INTERACTION\_NO\_EFFECT, VISUAL\_AMBIGUITY, MECHANIC\_UNCLEAR, SOFTLOCK\_SUSPECTED, UNREACHABLE\_SUSPECTED, OTHER. Severity: low, medium, high. |
| `finish` | `status`: gave\_up or blocked; `rationale` | Agent-declared stop. Success is detected by the environment, never by the agent. |

Bearing from a SAM pixel box (`x1..x2` in `0..W` where `W=640`): `bearing = atan((cx - W/2) / f)` with `cx = (left + right) / 2` and `f = (W/2) / tan(fov/2)`; take `fov` from the M0 calibration of `CAMERA_FOV`. A dev-only test compares bearings with ground-truth object directions (section 11, M2).

### 5.3 Prompting

- Versioned files `prompts/system_v1.md` and per-level sections; the prompt hash goes into the manifest. Freeze the prompt version before any sweep. Do not tune prompts to individual levels.
- Level 1 task statement, pinned verbatim by a test:

> You are an automated QA tester playing an unfamiliar first-person level. You are not told the objective. Work out what the level wants from what you can see, play it as a new player would, try to reach the end of the level, and report anything that confuses you or looks broken.

- Layout for caching: stable prefix first (system prompt, controls, level sections, L2/L3 briefing); dynamic suffix after (scratchpad, recent action log, thumbnails, current frame). Log `response.usage.input_tokens_details.cached_tokens` and `response.usage.output_tokens_details.reasoning_tokens` \[2\]\[6\].
- Frames: current frame full size; previous two as labelled thumbnails (`t-1`, `t-2`). Text history: last 20 decisions, one line each (moves, intent, frame\_change).
- Output is tool calls only (`tool_choice="auto"`). If the model answers in plain text, retry once with a reminder; a second miss counts as `no_action`.
- The controls list states primitives and units. It does not say what `use` does to doors or switches; that is a Level 2 mechanic.

### 5.4 Memory

Only two stores: the agent-written scratchpad and a harness-generated rolling action log built from the agent's own actions and visible feedback. No cross-episode memory except the explicit carry-over mode (section 8.5).

### 5.5 Guards (harness-level; logged; never choose actions)

- Budgets: `max_decisions`, `max_tics`, `max_usd` (checked after every response from usage times the price table), wall-clock timeout.
- Repeat detection: if the same primitive tuple is issued three times in a row with `frame_change` below 2 percent, add a text note to the next prompt (`Your last three identical actions changed nothing on screen. Try something different.`) and log a `stuck_warning` event \[9\].
- API errors: retry 429 and 5xx with exponential backoff and jitter starting at 500 ms, honouring `Retry-After`; never retry other 4xx \[2\]. After 5 consecutive failures end the run as `invalid_api_error`; invalid runs are excluded from success statistics and listed in the report.
- Empty or refused outputs: count them; abort after 5.

### 5.6 Model parameters

`muse-spark-1.3`, Responses API, `reasoning.effort` default `low` for the Pilot (M0 decides between `minimal`, `low`, `medium` on measured latency and behaviour), `high` for the Reviewer, `max_output_tokens` at least 3,000 because reasoning counts toward the cap, parallel tool calls allowed, `tool_choice="auto"`, `temperature` left at default (`1.0`). Model ID, effort, and variant (Standard or Contributor) are config values recorded in every manifest.

## 6. Information levels and code-level enforcement

### 6.1 Information-level definitions

The harness evaluates the exact same `muse-spark-1.3` Pilot agent, action space, and `sam-3.1` Grounder across three progressively richer information conditions. Only the prompt briefing and the optional structural floor plan change across levels:

| Dimension | Level 1 — Blind Play | Level 2 — Guided Play | Level 3 — Informed Play |
| --- | --- | --- | --- |
| **Player persona & task** | Pinned L1 task statement (section 5.3): infer objective and mechanics from pixels alone | Explicit scenario objective, win condition, and failure conditions | Explicit objective, win/failure conditions, plus structural level topology |
| **Controls specification** | Primitive names and units (`tics`, `degrees`) only; no mention of what `use` activates | Adds Doom interaction mechanics (`use` on doors, switches, lifts; keycards; hazard floors; pickups) | Same as Level 2 |
| **Visual inputs per step** | Current 640x480 RGB frame (`frame.jpg`) + two 320x240 thumbnails (`t-1`, `t-2`) | Same as Level 1 | Same as Level 1 + static 640x480 structural floor plan (`plan_l3.png`) generated at `tic=1` |
| **Spatial / map context** | None | None | Top-down sector map showing blocking walls, door thresholds, spawn marker/heading, goal/key markers, and dead-end labels |
| **Runtime tools** | `act`, `locate`, `note`, `report_issue`, `finish` | `act`, `locate`, `note`, `report_issue`, `finish` | `act`, `locate`, `note`, `report_issue`, `finish` |
| **Engine telemetry access** | Forbidden (`AgentObservation` only) | Forbidden (`AgentObservation` only) | Forbidden after `tic=1` static map render (`AgentObservation` only) |

### 6.2 Level 2 briefing contract (`scenarios/<id>/briefing_l2.md`)

Level 2 injects a static markdown block into the cached prompt prefix immediately after the controls table. Every L2 briefing follows a fixed 4-part template and is hashed into `manifest.json`:

1. **Objective**: Exact condition that ends the episode as a success (for example, *"Navigate the interconnected rooms and walk over the green armor vest (`GreenArmor`) to collect it before the 2,100-tic timer expires"* for `my_way_home`, or *"Reach the exit room, face the exit switch on the wall, and press `use`"* for `freedoom2_map01`).
2. **Core mechanics**:
   - `use` interacts with doors, wall switches, and lift platforms when the player is within approximately 64 map units and facing the surface. Standard doors slide open vertically for several seconds after `use`; switches change texture and trigger a door or lift.
   - Colored doors or bars require picking up the matching colored keycard or skull key first.
   - Green/bright floor slime (nukage/acid) inflicts periodic damage while standing on it; medkits restore health, whereas dark poison vials in hazard scenarios inflict 30 damage on contact.
   - Walking directly over an item (vest, medkit, key, ammo) collects it automatically without pressing `use`.
3. **Failure modes**: Episode timeout (`episode_timeout` tics) or player health reaching 0.
4. **Exclusions**: L2 briefings must never state room colors along the solution path, turn sequences, compass headings, or map coordinates.

### 6.3 Level 3 structural floor plan (`autoqa/doom/plan_renderer.py`)

At `tic == 1` of a Level 3 episode (before the first `observe()` call), `DoomEnv` renders a single static 640x480 PNG image (`plan_l3.png`) using `Pillow` from `state.sectors`, initial `state.objects`, and optional static landmark tags in `scenarios/<id>/annotations_l3.json` \[15\]:

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
4. **No live GPS tracking**: `plan_l3.png` is generated **once** at `tic == 1` and frozen for the remainder of the episode. Updating the player marker on every step would leak ground-truth odometry (`POSITION_X`, `POSITION_Y`, `ANGLE`) into the agent loop and violate Rule 3 (section 0.2). The agent must localize itself on the static floor plan using visual landmarks, just as a human tester reads a paper map.
5. **Timebox fallback**: If custom `state.sectors` polygon rendering takes longer than 45 minutes during M2, capture ViZDoom's native `state.automap_buffer` once at `tic == 1` with `AutomapMode.WHOLE` \[16\], overlay the static `S` and `G` markers, and immediately disable `automap_buffer` for all subsequent tics.

### 6.4 Code-level information firewall

Information-level separation is enforced by four mechanisms that are checked on every `make test` run:

1. **Strict module boundary**:
   - `autoqa/doom/types.py` defines `AgentObservation` (frozen dataclass with `decision_idx: int`, `tics_elapsed: int`, `tics_remaining: int`, `frame_jpeg: bytes`, `prev_frames_jpeg: tuple[bytes, ...]`, `frame_change_pct: float`, `plan_png: bytes | None`, and `frame_sha256: str`).
   - `autoqa/agent/*` and `autoqa/prompts/*` are forbidden from importing `vizdoom`, `autoqa.doom.env`, `autoqa.doom.telemetry`, or `autoqa.eval.*`. They receive a narrow `PlayerSession` protocol exposing only `observe() -> AgentObservation`, `execute(act_args) -> ActionFeedback`, `episode_finished() -> bool`, and `level: Literal[1, 2, 3]`.
2. **Runtime observation gate (`DoomEnv.observe`)**:
   - `observe()` reads only `state.screen_buffer` (converted from RGB24 numpy array `(480, 640, 3)` to JPEG bytes), computes `frame_change_pct` against the prior decision's grayscale frame, attaches the cached `plan_l3.png` bytes if and only if `self.level == 3`, and returns `AgentObservation`.
   - `state.labels`, `state.labels_buffer`, `state.objects`, `state.sectors`, and `game.get_game_variable(...)` are read exclusively inside `TelemetryRecorder.record_step()` (`autoqa/doom/telemetry.py`), which writes directly to `telemetry.jsonl` and returns `None` to `DoomEnv`.
3. **Prompt construction assertions (`autoqa/prompts/builder.py`)**:
   - When `level == 1`: asserts `briefing_l2 is None` and `obs.plan_png is None`; verifies the exact SHA-256 of the pinned Level 1 task string from section 5.3.
   - When `level == 2`: asserts `briefing_l2 is not None` and `obs.plan_png is None`.
   - When `level == 3`: asserts `briefing_l2 is not None` and `obs.plan_png is not None`.
4. **Request payload sanitizer (`autoqa/agent/client.py`)**:
   - Before any `POST /v1/responses` call, serializes the `input` payload and asserts that the number of `input_image` blocks is at most `1 + len(obs.prev_frames_jpeg) + (1 if level == 3 else 0) + (1 if cfg.overlay_in_context else 0)` and that forbidden telemetry keys (`pos_x`, `pos_y`, `labels_buffer`, `object_position`, `sector`) do not appear in any `input_text` block.