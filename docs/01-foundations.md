# Auto QA agent for ViZDoom: Meta-powered autonomous playtesting, information-level experiments, and QA reporting

*Project: Generalist Game QA, Meta Global AI Developer Hackathon. Drafted 4 Nov 2026 from current Meta Model API, ViZDoom, and V-JEPA documentation. Bracketed numbers point to the source list in section 14.*

This document contains the project's foundations and development plan. M0–M3.5 are implemented; M4's QA pipeline and carry-over mode are implemented with partial live validation. M5 remains unrun. Measured findings are in [M0](spike-results.md), [M1](m1-results.md), [M2](m2-navigation-review.md), [M3](m3-results.md), [M3.5](m35-results.md), and [M4](m4-results.md). Implementation does not imply full acceptance: the M3 sweep stopped early, grounding remains unreliable, and M4 retained only two valid carry-over pairs. The dated schedule and unexecuted design targets below are not claims of completed experiments.

## 0. Read this first

### 0.1 Time

A third-party listing gives the submission deadline as 5 Nov 2026, 6:30 AM IST, with judging 5 to 9 Nov \[21\]. Today is 4 Nov. Verify on the official portal, but plan as if only hours remain.

- Order of work is strict. After every milestone `main` must run end to end and be demoable. M0 + M1 + M2 is a submittable project. M3 and M4 raise the score. M5 is optional.
- **Schedule realism**: Implementing API tool loops, autonomous gameplay, SAM 3.1 segmentation, three information conditions, multi-seed sweeps, telemetry verification, and HTML reporting is a substantial scope. Any per-milestone hour figures in this specification are **strict timeboxing ceilings for triggering scope cuts, not reliable completion forecasts** (live API latency and sweep wall-clock time alone can consume several hours).
- No Docker, web server, database, task queue, or distributed runner. One Python package, one CLI, files on disk.
- At the end of every milestone: commit, `make test` green, and add a short entry to `docs/progress.md` (what works, what was cut, measured numbers).

Cut line (time left when M1 is green):

| Time left | Ship |
| --- | --- |
| under 6 h | M2 with Level 1 and Level 2 only, a plain HTML run report, demo recorded from real runs |
| 6 to 12 h | M2 with Level 3, M3, and a minimal M4 (issue log, verifier, HTML report; skip reviewer video) |
| over 12 h | all of M4, then M5 in listed order |

### 0.2 Non-negotiable rules

1. **Meta-only runtime AI.** Allowed: `muse-spark-1.3` (default), `muse-spark-1.3-contributor` (optional, cheaper), `muse-wonder-1.0` (optional reviewer ablation), `sam-3.1`, and V-JEPA 2 or 2.1 weights run locally for offline analysis. The `openai` Python package may be used purely as an HTTP client pointed at `https://api.meta.ai/v1`, which is how Meta's docs show it \[3\]. No OpenAI, Anthropic, Google, or other non-Meta model may be called. State this in the README technology table.
2. **No scripted gameplay policy & strict action integrity.** Every game action executed during an experiment run must originate from a Muse Spark tool call. Harness guards may inject text into the prompt (for example a stuck warning) but must never choose, modify, or insert actions during ordinary gameplay evaluation runs (`sweep_core.yaml`, `sweep_fast.yaml`, `sweep_carryover.yaml`).
   - **Explicit distinction for controlled defect-injection probes**: In Section 7, a separate diagnostic probe (`probe_broken_door` in `sweep_probes.yaml`) simulates a broken game build in which the engine's `USE` trigger fails to fire. This is an **environment-side fault injection** used solely to test whether the QA pipeline notices a broken interaction—never an agent-side policy or guard—and is governed by three hard rules: (a) it may only be enabled when `scenario.is_fault_probe == True` and `manifest.json` records `"fault_injection": "unresponsive_use_trigger"`, (b) it is forbidden in all ordinary gameplay runs, and (c) probe runs are never pooled into RQ1 or RQ3 gameplay performance statistics.
   - Calibration scripts in `scripts/` and determinism tests in `tests/` that replay recorded action traces must never be reachable from `autoqa run`.
3. **Information levels are enforced in code and tested** (section 6). Ground-truth engine state is for evaluation only and must never reach the agent.
4. **No invented results.** Every number in docs, README, reports, or demo comes from files under `runs/` or `results/` produced by real executions. If something was not run (including optional human reference playtests), the report and data files must explicitly record `null` / `"not run"`. Do not seed, cherry-pick, or hard-code outcomes, and never copy illustrative schema numbers into shipped artifacts.
5. **Do not rewrite the README.** At the end of M4, fill only its placeholder sections (Technology Overview, Quick Start, Demo link, Key Findings) from generated artifacts.
6. **Secrets.** API key only via the `MODEL_API_KEY` environment variable or a git-ignored `.env`. Never log it, never commit run folders that contain request headers.

### 0.3 Scope in one paragraph

A CLI-driven research harness in which a Muse Spark multimodal agent plays unfamiliar ViZDoom levels from pixels under three information levels, SAM 3.1 grounds what the agent decides to look for, and the system produces recordings, logs, trace-verified QA observations, and an experiment report that separates trace-confirmed behavioral events from causal hypotheses (agent limitations, intentional design friction, or candidate level defects).

## 1. Research questions and where they are answered

| RQ | Answered by |
| --- | --- |
| RQ1 Can Meta models navigate and complete unfamiliar scenarios? | M1 agent; M3 metrics (success, progress, coverage) per information level |
| RQ2 Can they recognise and document failures? | `report_issue` tool during play and Reviewer pass after play (M4) |
| RQ3 Does structured feedback improve later attempts? | Carry-over mode in section 8.5 (M4) |
| RQ4 Are the observations actionable? | Issue schema, deterministic trace verifier, causal triage hypotheses, replay clips, behavior-confirmation rate (M4) |
| RQ5 Do AI difficulties match human difficulties? | Information-sensitivity classification plus optional human reference file (M4). Neither the verifier nor the report ever equates agent failure with a proven game-design defect or agent completion with human playability. |

## 2. Verified technical facts

### 2.1 Meta Model API

- Base URL `https://api.meta.ai/v1`. OpenAI-compatible Responses, Chat Completions, and Messages surfaces. Key from the dashboard; Meta's samples read it from `MODEL_API_KEY` \[1\]\[3\]. The Responses API is the recommended surface and is the one to use \[3\]\[6\].
- `muse-spark-1.3` is the model Meta recommends for new work: 1,048,576-token context, text/image/video/audio/PDF in, text out, reasoning effort `minimal` to `max`. Effort `none` is not supported and returns HTTP 400 \[1\]\[6\]. Audio understanding on 1.3 is not fully supported, so this project uses no audio \[1\]\[4\].
- `muse-spark-1.3-contributor` is the same model at much lower prices, but content may be used to train future Meta models; its rate limits are 150 RPM and 3,000,000 TPM per team, and `reasoning.effort="max"` is not available on Contributor variants \[1\]\[2\]\[6\].
- Prices per 1M tokens, Standard: input $1.25, cached input $0.15, output $4.25. Contributor: $0.10, $0.002, $0.20. SAM 3.1: $2.50 per 1,000 images ($0.20 per 1,000 video frames, 50-frame minimum per video request). Limits for `muse-spark-1.3`: 3,000 RPM and 4M TPM per team; `sam-3.1`: 3,000 RPM \[2\]. Reasoning tokens count toward the output cap and bill at the output rate (`response.usage.output_tokens_details.reasoning_tokens`) \[6\]\[24\]. A third-party report says free accounts are capped far lower (60 RPM); read the real limits from the `x-ratelimit-*` response headers \[2\].
- Images: `input_image` as URL, base64 data URL, or `file_id`; up to 50 images per request; JPEG, PNG, WebP, GIF; images only in user-role messages; roughly 1,300 to 1,500 input tokens for a 1280 px image; exact counts via `POST /v1/responses/input_tokens` \[3\].
- Visual grounding: Muse Spark can return points and boxes on a normalized 0 to 1000 grid; prompt patterns are in \[3\].
- Video: upload an mp4 through the Files API (`purpose` `user_data`) and reference it with `input_file`, or pass `input_video` \[4\].
- Structured output: Responses `text.format` with a JSON schema; function-tool `parameters` follow the same limits; recursive schemas are rejected; nesting depth max 10 \[5\]. On `/v1/responses`, only `tool_choice="auto"` is supported (`"required"`, `"none"`, and named functions return HTTP 400), and `seed` is not a supported parameter \[5\]\[6\].
- Tool loops with a reasoning model: raw reasoning is private, so to preserve chain-of-thought across stateless tool turns on `/v1/responses`, pass `include=["reasoning.encrypted_content"]` on each request (with `store=False`) and replay the returned `reasoning` items (including `encrypted_content` and `summary`), any `phase="commentary"` assistant messages, `function_call` items, and matching `function_call_output` items from `resp.output`—or chain server-stored turns via `previous_response_id=resp.id` (never combine both in the same request, which returns HTTP 400) \[5\]\[6\].
- Meta's agent-loop guidance: bound loops by max iterations, token budget, and stuck detection (hash tool name plus arguments, stop on the third repeat) \[9\].
- Prompt caching: keep a stable prompt prefix and read `response.usage.input_tokens_details.cached_tokens` \[2\]\[6\].
- Availability: Meta enables the service per jurisdiction. The Geographic Use Policy lists general restricted territories and restricts `Muse Image` in India \[10\], while the Models documentation notes `Muse Wonder` is unavailable in South Korea \[1\]; `muse-spark-1.3` and `sam-3.1` have no model-specific exclusions listed, though account access remains at Meta's discretion. The July launch preview was described as US developers only \[11\], so confirm your own key works (section 13).

### 2.2 SAM 3.1 on the Meta Model API

- Model `sam-3.1` over the Responses API. Input is a short noun phrase plus an image or video; output is special-token text with a box and a pixel mask per match. One concept per request; several concepts need several requests \[7\].
- Python parser: the project installs `meta-sam-parser>=0.1.0` through `uv`. Stream requests with `stream=True` and pass `metadata={"mask_encoding": "one_bit", "include_confidence": "true"}` (`metadata` values must be strings `"true"`/`"false"`; passing boolean `True` returns HTTP 400) \[7\]. The installed library exposes `parse_responses_stream(..., image_segmentation_format()).final_result()`; the original `parse_image_stream` name is not available. Decode `one_bit` masks with `decode_mask_to_raster` \[8\]. See the [response investigation](sam-investigation.md).
- The original design handed a Muse-selected concept directly to SAM \[7\]. Text-guided localization returned no masks on the tested Doom prompts. M2's experimental default instead uses Muse-generated boxes followed by SAM segmentation; direct text-SAM remains selectable. Both modes return uncertain candidates, not verified identities. The [paired evaluation](m2-results.md#grounding-investigation-and-choice) measures the hybrid's misses and false positives.

### 2.3 V-JEPA

- V-JEPA 2 (June 2025) and V-JEPA 2.1 (March 2026) are self-supervised video encoders from Meta FAIR, code under MIT. Checkpoints load through Hugging Face Transformers (for example `facebook/vjepa2-vitl-fpc64-256`) or `torch.hub` (for example `vjepa2_1_vit_base_384`) \[12\]\[13\].
- V-JEPA 2-AC is a world model post-trained on robot-arm trajectories with 7-DoF actions. It does not apply to Doom and is not used \[12\].
- V-JEPA is limited to offline embedding analysis in M5. It is not in the runtime agent loop.

### 2.4 ViZDoom

- The project pins ViZDoom 1.3.2 (which fixed `labels_buffer` bounding-box dimensions) and Python 3.12 (`>=3.12,<3.13`) in `pyproject.toml`. Setup uses `uv` in WSL/Linux; other platforms have not been validated for this project \[20\].
- In `PLAYER` mode the engine waits for the agent's action, so model latency does not change game dynamics. `SPECTATOR` mode lets a human play while ViZDoom records states, actions, and tics. `ASYNC_*` modes run at a fixed ticrate \[16\]. Use `PLAYER` for agent runs (and `SPECTATOR` only if recording optional human reference sessions). One tic is 1/35 s \[14\].
- `make_action(action, tics)` sets the action, advances the tics (repeating the action vector on every processed tic), and returns the reward \[14\].
- `new_episode(recording_file_path)` records the episode as a `.lmp`; `replay_episode(path)` replays it with all states and rewards available, and it can be re-rendered at another resolution or with the HUD on. Sending a game command or calling `make_action` interrupts playback; advance replays with `advance_action()` \[14\]\[19\]. Calling `new_episode()` again is currently required to flush the recording \[19\].
- `set_seed` seeds the RNG that generates episode seeds \[14\].
- Buffers: screen, depth, labels (object name, category, bounding box), automap, audio; state also carries `objects` and `sectors` (each sector has lines with `is_blocking`) when enabled via `set_labels_buffer_enabled(True)`, `set_objects_info_enabled(True)`, and `set_sectors_info_enabled(True)` \[14\]\[15\]. Depth and labels contain noise, and POSITION, ANGLE, and CAMERA\_FOV variables are unavailable, when `viz_nocheat` is enabled \[14\]\[16\]. Keep it disabled so evaluation telemetry works.
- Buttons are binary (`MOVE_FORWARD`, `TURN_LEFT`, `USE`, `ATTACK`, `SPEED`, and so on) or delta. `TURN_LEFT_RIGHT_DELTA` values are degrees; `MOVE_*_DELTA` values are map units \[16\].
- Original-style levels: load `doom.cfg`, `doom2.cfg`, `freedoom1.cfg`, or `freedoom2.cfg` from the scenarios directory, then `set_doom_map(...)` and `set_doom_skill(1..5)`. The default reward is 1 for reaching the end of the level. ViZDoom cannot ship original Doom WADs, but bundles `freedoom2.wad` (`MAP01`–`MAP32`, used with `freedoom2.cfg`) inside `vizdoom.install_path` out of the box; `freedoom1.wad` (`E1M1`–`E4M9`) requires a separate download \[14\]\[17\].
- Bundled scenario `my_way_home`: a series of differently coloured rooms plus one dead-end corridor, a green vest in one fixed room, random spawn room and heading, episode ends on the vest or timeout \[18\].

### 2.5 Live empirical checks: M0 must measure and record each item

Write results to `docs/spike-results.md` as measured values or `could not verify: reason`. Do not assume.

1. `GET /v1/models` lists `muse-spark-1.3` and `sam-3.1` for the team key; credits remaining; real `x-ratelimit-*` values.
2. A function-calling round trip on the Responses API with a 640x480 Doom JPEG in the user turn, passing `include=["reasoning.encrypted_content"]` and replaying reasoning + `function_call` + `function_call_output` items back (`tool_choice="auto"` and parallel tool calls) \[5\]\[6\].
3. Latency, reasoning token count, and total token usage per decision for a 640x480 JPEG at effort `minimal`, `low`, `medium` (leaving `temperature=1.0` at default; `seed` is unsupported on `/v1/responses`) \[6\].
4. Confirm `freedoom2.wad` is present in `vizdoom.install_path` and `freedoom2.cfg` with `MAP01` runs headlessly \[17\]; optionally check if `freedoom1.wad` is installed or download it if `E1M1` is desired.
5. Whether monsters can be disabled cleanly for navigation-only runs on `freedoom2_map01` via `add_game_args("-nomonsters")` without desynchronizing `.lmp` replay. Otherwise use `doom_skill=1`. Whichever is chosen is an experimental control and must be documented.
6. Turn and movement calibration: degrees actually applied by a single-tic `TURN_LEFT_RIGHT_DELTA` (`make_action([..., deg], tics=1)`), whether large values are clipped by the button max value, forward speed in map units per tic with and without `SPEED`, and the exact value of `CAMERA_FOV` at 640x480 \[16\].
7. Exact `Label.object_name` strings in `state.labels` and `Object.name` strings in `state.objects` for the `my_way_home` vest (`GreenArmor`), `health_gathering_supreme` medkits and poison vials, and `freedoom2_map01` actors (noting that Doom doors and switches are sector linedefs in `state.sectors`, not actor labels) \[15\].
8. Record a `.lmp`, replay it at 1280x720 with HUD using `advance_action(1, True)`, and confirm identical final tic and reward; capture frames during replay following the official example \[19\].
9. SAM 3.1 on Doom frames: 20 labelled frames across `my_way_home`, `health_gathering_supreme`, and `freedoom2_map01`, testing noun phrases (`green armor`, `vest`, `door`, `switch`, `medkit`); parse with `meta_sam_parser` and record IoU hit rate, confidence distribution, and latency \[8\].
10. Files API upload of an H.264 mp4 (`-movflags +faststart`) of up to 2 minutes (`purpose="user_data"`) and Muse Spark video understanding on it; measure upload + inference latency \[4\]\[7\].

## 3. Architecture and Meta model roles

The diagram includes the implemented M3 metrics and opt-in M4 QA pipeline, alongside the Pilot, experimental grounder, telemetry, inline issue candidates, native recording, and replay. Validation limits are recorded in the linked milestone reports; source issue records are preserved and verification writes derived outputs.

```text
ViZDoom (PLAYER mode) --frames--> Pilot loop (agent/pilot.py) <--tool calls--> Muse Spark 1.3
   |   ^                              |   locate(concept) ----> SAM 3.1 (Grounder)
   |   +--- act(primitives) ----------+   note / report_issue / finish
   |
   +--> telemetry (eval only, never to agent) --> eval/metrics.py --> results/
   +--> episode.lmp + frames + events.jsonl --> qa/reviewer.py (Muse Spark, mp4 + log)
                                              --> qa/verify.py (trace behavior checks + causal triage)
                                              --> report.html, issues.verified.json, dossier
```

| Role | Meta model | What it does | Cadence |
| --- | --- | --- | --- |
| Pilot | `muse-spark-1.3` | Sees frames, reasons, calls `act`, `locate`, `note`, `report_issue`, `finish`. The only source of game actions. | every decision (1 to 4 API calls) |
| Grounder | Muse Spark + `sam-3.1` (experimental default); direct `sam-3.1` optional | Muse proposes boxes from the current image and concept; SAM segments those boxes. The harness converts masks to candidate bearings, size, and confidence. Candidate identity remains unverified. | on demand via `locate` |
| Reviewer (M4) | `muse-spark-1.3` at high effort (optional, unrun M5 ablation: `muse-wonder-1.0`, preview, Standard mode only \[1\]) | Opt-in player-visible video/keyframe and log review with structured findings and step citations; trace checks run separately. | post-episode, only when explicitly invoked |
| Offline embedder (M5, not implemented) | V-JEPA 2 ViT-L via Hugging Face | Planned frame-window embeddings to estimate revisits and novelty without engine telemetry, for comparison against ViZDoom ground truth. | offline only; not run |

Deliberately not used: Muse Image, Muse Voice Transcribe, Muse Spark audio, V-JEPA 2-AC. Muse Glimmer (30B dense, text and image, Apache-2.0, runs locally) and Muse Spark Open are contingency backends only if API access is blocked \[22\].

The implemented engine boundary exposes a narrow `PlayerSession` (`observe`, `execute`, `episode_finished`, `level`) to the Pilot. Telemetry and lifecycle methods remain on the driver side. Supporting another game engine is future work; Meta Horizon support is not implemented.
