# M3.5 — Combat and varied-map gameplay

Work for [issue #13](https://github.com/Ramsi-K/vizdoom-agent-research/issues/13), separate from the M3 benchmark. These are bounded exploratory attempts, not a matched information-level comparison or a reliability estimate. M3 measurements and reports are unchanged.

## Implementation and protocol

Combat is opt-in through four new scenario profiles. Existing M0–M3 profiles retain their action schemas and prompts. Muse Spark receives a control reference covering attack, simultaneous firing with movement/turning, vertical aim, weapon selection/cycling, jump, crouch, reload and alternate attack. The reference explains units and engine availability; it does not provide target priorities, combat tactics, routes or action sequences. Unowned weapons are not granted, and unsupported controls may have no effect.

The existing Pilot, Meta client, L2 briefing, hybrid Muse-box/SAM grounding, model-written landmark/outcome ledger, navigation updates and visual-cycle warnings are reused. Grounding candidates retain uncertain identity. Engine labels, positions, combat counters and inventory are collected for offline evaluation only. Actions remain model-selected and are validated at the engine boundary; no scripted combat, pathfinding or navigation is used. Combat runs also record non-reasoning model output items and match executed primitives to model `act` calls.

Initial protocol: seed 101, L2, Muse Spark 1.3, low effort, hybrid grounding, 60 decisions, 900 wall seconds, 45-second request timeout, 3,000 maximum output tokens, USD 0.60 recorded-usage threshold per attempt and USD 2.40 across the initial plan. Thresholds are checked after usage arrives, so a final response can cross a threshold; failed-request charges may be unknown. Each invocation can launch only one selected episode. A later episode requires another explicit invocation.

| Profile | Bundled scenario | Objective | Engine horizon |
| --- | --- | --- | ---: |
| `basic_combat` | `basic` | Eliminate the target | 300 tics |
| `defend_center_combat` | `defend_the_center` | Survive the full horizon | 2,100 tics |
| `deadly_corridor_combat` | `deadly_corridor` | Reach the armor | 2,100 tics |
| `freedoom2_combat_map01` | `freedoom2`, MAP01 | Activate the exit | 4,200 tics |

All four enable monsters and skill 3. Exit completion uses native termination, not positive shaping reward. Survival completion requires reaching the full scenario horizon alive. A decision/cost stop is not survival success. HUD screenshots provide immediate combat information; no combat counters are added to the agent observation.

The relevant control definitions and scenario mechanics are documented in the official [ViZDoom enums](https://vizdoom.farama.org/api/python/enums/) and [bundled environments](https://vizdoom.farama.org/environments/default/). Actual control effects were checked locally rather than assuming every exposed button works on every map.

## Recording startup fault and recovery

The first basic run completed. Subsequent arena and corridor attempts hung before the first observation or model-client construction. Their event and telemetry logs are empty; they are setup failures, not autonomous gameplay failures, and have no API calls from this application. The unfinalized directories and plan checkpoints are preserved locally.

A saved Python stack trace located the wait in `DoomEnv.__init__`, at native `new_episode(recording_path)`, after `init()` had returned. An offline comparison on the installed ViZDoom 1.3.2/WSL setup recorded successfully with a 105-character filename and hung with a 115-character filename. The affected live paths were 113–115 characters. This establishes a local recording-path failure; it does not establish a universal filename limit across ViZDoom versions or platforms.

The fix passes a short temporary symlink to native recording and replay when the original absolute path exceeds 100 bytes. The artifact is still written into its original run folder; the alias is removed after the engine closes. A regression check records and replays compound controls from a directory longer than 115 characters. Both short and long variants pass. The basic attempt retains source checkpoint `624a3c0`; resumed attempts use `6787787`. Source hashes remain in each manifest; the original experiment checkpoint is not rewritten to claim the new source.

## Offline validation

After the recording fix: **142 tests passed, four live API tests deselected**, in 13.39 seconds. Ruff lint and formatting pass. Checks cover legacy prompt/tool hashes, opt-in schemas and bounds, native pitch direction, simultaneous movement/firing, owned weapon switching, unowned weapon requests, model action provenance, reasoning-output exclusion, natural death/success clocks, native combat-counter agreement, full MP4 export and long recording paths.

Measured pitch calibration: native delta +15 changed engine PITCH by approximately −14.9963 degrees; delta −30 changed it by approximately +30.0036 degrees. `look_up` therefore sends a positive delta. Test fixtures that use privileged labels to verify a native kill are offline control tests only; they are not autonomous gameplay evidence.

## Initial autonomous results

Four autonomous attempts completed. **Total recorded cost: USD 1.28339375**, including USD 0.06004100 for grounding. No API or grounding errors were logged in these runs, and no follow-up had been launched at this initial checkpoint. Costs are recorded usage estimates, not a separately reconciled invoice.

| Scenario | Outcome | Executed acts | Played tics | Kills | Final health | Recorded USD |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Basic | Objective completed | 1 | 6 | 1 | 100 | 0.01709965 |
| Defend the center | Cost threshold; survival incomplete | 51 | 1,123 | 6 | 6 | 0.60154180 |
| Deadly corridor | Player death; armor not reached | 6 | 115 | 1 | −14 | 0.06176180 |
| Freedoom 2 MAP01 | Cost threshold; exit not reached | 54 | 755 | 2 | 74 | 0.60299050 |

**Basic:** Muse requested a ten-tic attack on its first decision; the native objective ended after six played tics. Replay confirms one kill and five damage dealt. The engine resets the terminal clock to zero, so elapsed tics are reconstructed from native playback. This is a simple, short shooting control, not a demonstration of route finding or sustained combat.

**Arena:** Muse dealt 55 damage, took 94 damage and exhausted its initial 26 rounds of selected-weapon ammunition. At decision 30 it explicitly noted the ammunition shortage and requested weapon cycling/backward movement. It then requested the fist slot and retreating at decision 31. One cycle request and three fist-slot requests produced **no selected-weapon transition**; the recorded inventory owns only slot 2. The model continued movement with 6 health. It visited 28 sampled 128-unit cells, but that is movement within an arena, not evidence of exit progress. The final response crossed the soft USD 0.60 threshold at USD 0.60154180; it was charged, and its returned `note` call was not executed. No later action was executed. There were 52 decision attempts and 51 executed acts. The 1,123-tic interval is 32.09 seconds, short of the full survival requirement.

**Corridor:** Muse killed one enemy and dealt 15 damage, but took 114 damage and died with engine health −14 before collecting the armor. It executed six acts over 115 tics (3.29 seconds). Neither a kill nor movement shaping reward is counted as objective completion.

**Freedoom MAP01:** Muse spent its early decisions moving, turning and trying interactions. Its first attack was at decision 36, following a turn toward visible figures. It eventually recorded two kills, 40 damage dealt, 27 damage taken and native ITEMCOUNT 1. Selected-weapon ammunition ended at 45; final health was 74. It visited 15 sampled 128-unit cells and made 11 `use` attempts, but did not reach the exit. The cost threshold ended it after 55 decision attempts and 54 executed acts, over 755 played tics (21.57 seconds). The final response cost is included; its returned tool call was not executed.

**Controls available versus used:** Across these attempts, simultaneous turning/firing was used in two corridor primitives. Simultaneous translation/firing, vertical aim, jump, crouch, reload and alternate attack were not used live. Weapon cycling/slot selection were requested in the arena but did not change the selected weapon. Corridor's initial −1→2 selection is native weapon equip, not demonstrated model-directed switching. Native offline tests establish control wiring and owned-weapon switching; these live traces do not establish effective use of the entire control set.

**Tool failures and memory:** The existing model-written ledger and navigation telemetry remain active. Basic/arena/corridor/Freedoom recorded 1/47/3/44 ledger and navigation updates and 1/3/1/5 `locate` calls. Arena had one rejected tool batch and two navigation rejections; Freedoom had three rejected tool batches and three navigation rejections. All four batch failures supplied `run` as a string instead of a boolean. Navigation rejections concerned missing/recent grounding references or expired plans. These calls were not silently repaired into gameplay actions. They are observed protocol failures, not proof that any one failure caused an incomplete objective. Every executed primitive in all four runs matches a recorded model `act` request.

In this initial four-run plan there is one completed basic objective, no completed arena survival horizon, no corridor armor pickup and no Freedoom exit. Native counters establish combat events; they do not support a claim of reliable level completion or an improvement over M3's different scenarios/protocol.

These completed runs retain full native recordings, full MP4s and matched terminal combat counters. The basic MP4 contains six gameplay frames plus the labeled recap/result hold; the arena/corridor/Freedoom exports contain 1,123/115/755 gameplay frames without a success hold.

## Post-run grounding audit

An offline audit of all four arena candidate masks replays the native demo at 640×480 using the original primitive update boundaries. At all three audited decision frames, the JPEG SHA-256 exactly matches the image sent to the live models. Engine label masks are used only in this post-run audit, never as agent input.

| Decision | Model concept | Candidate | Enemy-label mask IoU |
| --- | --- | --- | ---: |
| 0 | distant soldier | 1 | 0.000000 |
| 20 | distant red enemy | 1 | 0.783562 |
| 31 | pink worm enemy | 1 | 0.856681 |
| 31 | pink worm enemy | 2 | 0.857538 |

The first candidate lies below the visible enemy despite a nonempty SAM output. Later candidates overlap enemy labels, but their success does not show that grounding caused kills or improved survival. These are four post-hoc candidates from one episode, not an independent accuracy benchmark. Every live candidate retained `identity_verified=false`.

For reproducibility, one-tic-at-a-time playback produced different rendered image hashes at later frames even at the correct logged tic. Matching the original primitive-sized playback updates reproduced the original JPEG hashes; only those matched frames are used for the IoUs above.

## Extended demo episode — separately authorized follow-up

The user authorized one further autonomous Freedoom MAP01 episode for the hackathon demo, with **300 decisions, 21,000 tics and a USD 3 recorded-usage stopping threshold**. They separately approved raising the wall limit from 900 to 3,600 seconds so inference latency would not prematurely stop the longer attempt. The run starts with fresh memory and seed 101; it does not resume the earlier trajectory or import its notes.

The initial Freedoom manifest and this run have identical model, effort, L2 prompt, briefing and control hashes, skill, button ordering, grounding mode, per-request timeout and output-token limit. Only the four approved budget/horizon settings differ. The necessary configuration validator change allows up to 21,000 tics only for `freedoom2_combat_map01`; other profiles retain their 4,200-tic ceiling. No agent, prompt, control, grounding or action-selection code changed for this extension. The live source checkpoint is `8a02c44` and the configuration is [`m35-freedoom-extended.yaml`](../configs/m35-freedoom-extended.yaml).

The single attempt is `20261104T193056.881681Z_freedoom2_combat_map01_L2_s101`. **It was interrupted by SIGTERM (worker exit 143), before any approved stopping limit or level completion.** The source of the signal was not logged and is not established. Activity stopped after roughly 30 minutes; attributing that to an execution-environment limit is plausible but unverified. No controller termination was issued, and no further paid attempt or resume was launched.

| Extended attempt, preserved prefix | Measured result |
| --- | --- |
| Executed acts / primitive inputs | **215 / 296**, through decision 218 |
| Saved observations | **220**, including observation 219 without a logged model response |
| Native played interval | **3,144 tics / 89.83 seconds** |
| Combat counters | **4 kills, 12 hits, 122 damage dealt, 18 damage taken** |
| Final state | **86 health, 0 armor, selected slot 2, 27 rounds** |
| Recorded usage | **USD 2.09286690**, including USD 0.08065475 grounding |
| Goal / stopping status | **Exit not reached; interrupted, not cost/decision/tic exhaustion** |

Two grounding attempts, at decision **210 (`hallway enemy`)** and **217 (`metal double doors`)**, returned `APIConnectionError` / `could_not_verify`. They did not yield successful mask evidence. The final observation has no logged response, so an unlogged in-flight request may also have incurred a charge. **USD 2.09286690 is known completed usage, not a verified total invoice or a guarantee that all failed requests were free.** Known recorded M3.5 usage including the initial four attempts is USD **3.37626065**. The extended attempt's USD 3 threshold was separate from the initial plan.

Follow-up validation: **147 offline tests passed**, four live API tests deselected, in **12.44 seconds**; Ruff lint and formatting pass. Recovery additionally verified the real preserved prefix as described below.

### Original recording loss and verified offline recovery

SIGTERM prevented normal finalization: the original manifest still says `running` with its initial zero-cost placeholder; the original `.lmp`, final memory file and MP4 were absent. Those original files and logs were left unchanged. The experiment checkpoint now records interruption and partial cost accounting and cannot automatically retry the slot. The native capture requirement was therefore **not fully met** for this attempt.

[`scripts/recover_m35.py`](../scripts/recover_m35.py) created a **separately labelled, offline reconstructed prefix** using only the 296 input vectors/tic durations already recorded from original model `act` calls. It imports no model client and chooses, repairs or adds no gameplay inputs. Recovery required:

- Exact JPEG SHA-256 matches for **all 220 original saved observations**.
- Matching native variables at **all 296 primitive samples**, including position/orientation, health, ammunition/inventory, hit/damage/kill counters, sector heights, clock and reward.
- A matching original model request for every replayed primitive, and unchanged source manifest/event/telemetry hashes.

The resulting reconstruction is stored under the original run's `reconstructed-prefix/`, with its own `manifest.json`, `reconstruction.json`, `episode.lmp`, `replay.mp4` and `replay.json`. Independent native demo playback matches the original logged final tic/reward and combat counters. The full recovered video contains **3,144 gameplay frames at 35 fps, 1280×720 H.264**, with no invented ending. It reproduces the complete logged executed interval; it is **not the missing original native recording**, and it does not show completion of the requested 300-decision experiment. The original final memory file remains unavailable; model-written ledger updates survive in the original event trace.

### Extended navigation and combat findings

The reconstructed prefix audit verifies 220 exact observation frames, 23 observed pistol-ammunition decrements, 12 damaging and 11 non-damaging events. Two discharges occurred without a visible enemy-category sprite; only five had horizontal enemy-bbox overlap. As with the initial runs, these are discharge/geometry measurements, not exact ballistic accuracy. No other weapon was selected. Native ITEMCOUNT ended at 1; health and ammunition changes are tracked separately rather than treating this as a count of every physical pickup.

There were 15 `locate` attempts: 13 completed receipts (11 nonempty, with 14 saved masks; two empty), and two failed with connection errors. Empty lookups at decision 40 (`exit door`) and 189 (`open doorway`) had no Muse box proposals and no SAM request/stream. They are not successful detections or SAM decoding failures. The successful `red enemy` candidate at decision 20 overlaps a Zombieman at IoU **0.8063**. The other saved queries concerned doorways, buttons, stairs, lights, a cross, metal/white doors or an AGM sign. Their lack of enemy-label overlap does **not** establish false detections; their interactive/route semantics remain unverified. The fourth kill occurred after the failed `hallway enemy` lookup, without a successful mask for that request; the later failed door query was unrelated to that kill. The unchanged visual/tool protocol continued, rather than a scripted fallback.

| Logged decisions | Known USD | Executed acts | First-ever sampled cells | Cell reentries | Cumulative kills |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0–59 | 0.53109860 | 60 | 15, including spawn | 10 | 2 |
| 60–119 | 0.61835730 | 60 | 4 | 38 | 2 |
| 120–179 | 0.59654615 | 59 | 0 | 29 | 2 |
| 180–218 | 0.34686485 | 36 | 3 | 14 | 4 |

The prefix visited **22 sampled cells**, with **113 entries / 91 reentries**, 125 translation primitives, two low-displacement collision proxies, 35 `use` primitives and three geometry-changing-use proxies. Three visual-cycle warnings were logged. After the first 60 decisions, USD **1.56176830** of completed usage added seven cells and two further kills, but no exit. The 120–179 block added no cells despite substantial movement/revisiting. This supports a mixed finding: some extra exploration and a later fight, alongside prolonged local navigation loops. This fresh stochastic trajectory differs before the old stopping point, so it is not a controlled causal estimate of the benefit of increasing the budget.

A concrete localization failure appears at decisions 139–140. Telemetry confirms a requested 180° left turn changed heading from **171.0791° to 351.0791°**, yet the next ledger text said **“AGM still centered, orientation confused.”** Original before/after screenshots show different AGM-labelled structures. At decision 155 the ledger explicitly mentions the cycle warning for decisions 130/118. These observations establish that turn controls, memory and warnings were active; they are consistent with landmark ambiguity and ineffective interpretation/use of existing memory, rather than missing functionality. They do not prove why every revisit occurred or identify a game defect.

The operational failure also needs a targeted future fix: a long-run launch must honor the approved lifetime and finalize buffered native recording on termination. That runtime change was **not made during this frozen experiment**. The offline recovery utility preserves this attempt's reviewable evidence without a paid rerun or a scripted gameplay fallback.


## Offline gameplay audit of the four initial runs

This audit was requested after the original findings. It adds interpretation and measurements without changing the original traces, results or agent. [`scripts/audit_m35.py`](../scripts/audit_m35.py) makes no model requests. It verifies source artifact hashes, reproduces **all 114 original observation JPEGs exactly** using the recorded primitive update schedule, and audits all 12 saved candidate masks. A separate one-tic playback pass measures ammunition consumption, native damage/hit/kill counters and visible label categories. Both playback passes use recorded demo inputs only; privileged labels and audit findings are never supplied to the live agent.

ViZDoom documents [hit/damage counters](https://vizdoom.farama.org/api/python/enums/#vizdoom.GameVariable) and [label categories and pixel bounds](https://vizdoom.farama.org/api/python/game_state/#vizdoom.Label). Here an observed discharge means a positive selected-ammunition decrease while the weapon remains the same across a native tic. All initial observed discharges use weapon slot 2. Same-tic pickups can hide consumption, and these are not counts of requested attack tics.

### Aiming, non-damaging fire and target detection

| Initial run | Observed discharges | Damaging | Non-damaging | Discharges with no visible enemy-category sprite | Horizontal enemy-bbox overlap |
| --- | ---: | ---: | ---: | ---: | ---: |
| Basic | 1 | 1 | 0 | 0 | 1 |
| Arena | 26 | 6 | 20 | 6 | 7 |
| Corridor | 7 | 1 | 6 | 0 | 0 |
| Freedoom | 10 | 4 | 6 | 2 | 1 |

The damaging-discharge fractions are 1/1, 6/26, 1/7 and 4/10 for these particular recordings. They are **not general weapon accuracy estimates**. A horizontal overlap means the viewport's center x-coordinate intersects a visible Monster/Player-category bounding box before the discharge; it ignores vertical aim, bullet spread, autoaim, target depth and whether a death-animation sprite is still alive. Several damaging shots occur with a small horizontal gap, so treating bbox overlap as a hit detector would be wrong. All four runs use the measured 90° camera FOV. Sprite categories exclude the player's weapon and explicitly categorized gore/corpses.

Non-damaging discharges are candidates for wasted ammunition, not automatic proof that an action had no tactical purpose. The clearest observed failures are:

- **Arena 00:00.69–00:01.89:** four discharges after the first kill produced no damage and had no visible enemy-category sprite at discharge. The model continued describing a distant target even when later decision frames showed none.
- **Arena 00:07.43–00:09.71, decisions 20–25:** `locate("distant red enemy")` returned a candidate with bearing −10.0999° and exact-frame enemy-mask IoU 0.783562. All six executed acts were attack-only, with no turn. Six rounds were consumed, no damage was dealt, and the observed horizontal bbox gaps at discharge were roughly 10.6–16.9°. The model's intents explicitly called the target left. This is direct evidence of ineffective use of available grounding, rather than a failed mask on that target.
- **Arena ammunition/health collapse:** the 26 observed rounds were exhausted by about 00:10.43. Five further ATTACK-held tics occurred with an empty selected pistol, which are dry-input time, not additional shots. At approximately 00:12.40–00:13.09 the player took 94 damage and fell to 6 health. Weapon-switch requests never changed the selected weapon; later movement preserved 6 health until the budget stop. Wasteful firing plausibly contributed to the later resource problem, but this run does not prove a different firing policy would survive.
- **Corridor:** seven discharges yielded one damaging event. The agent turned while firing twice but executed **no translation primitive**. Its horizontal gap narrowed from about 15° to 3.58° before the killing hit, while it was taking fire; it died after 3.29 seconds. Stationary exposure and imperfect alignment are plausible contributors, not a proved counterfactual explanation.
- **Freedoom:** the first enemy-category sprite appeared in original decision frame 35; Muse requested enemy grounding and a right turn at that decision, then first attacked at decision 36. This supports recognition and response to visible enemies. The turn was 20° right; the correctly aligned candidate reported +12.94° and the false health-item candidate +34.93°. The first discharge afterward had a 5.89° horizontal gap to the nearest enemy bbox and dealt no damage. This is consistent with imperfect target selection/alignment, but the intended target cannot be established from the turn alone. Ten discharges produced four damaging events and two kills; two discharges had no enemy-category sprite visible at that instant. The trace demonstrates engagement, not precise target tracking throughout each held burst.

### SAM alignment and uncertain identity

All 12 mask audits use exact original image hashes. Object-mask IoU is suitable for labeled sprites, while doors, wall panels and openings generally lack corresponding object-instance masks in this label buffer. A near-zero enemy IoU for a passage/door query is therefore **not** a localization failure.

- Basic's `enemy monster` candidate overlaps the Cacodemon label at IoU **0.8883**.
- Arena has the four audited candidates documented above: one background miss, then three enemy overlaps at **0.7836–0.8575**.
- Corridor queried `corridor passage`, not an enemy. Its broad geometric candidate has no meaningful enemy-instance IoU; interactive/passable semantics remain unverified.
- Freedoom's first four calls queried an opening, metal door, orange wall switch and orange lit panel. Visual inspection shows candidates on the referenced structures. This does not verify that a panel is an actionable switch or that a door will open from the agent's position.
- Freedoom decision 35 queried `enemy soldier` and returned two candidates. One overlaps a Zombieman at IoU **0.6444**. The other overlaps a **HealthBonus** at IoU **0.6887**, but an enemy at only **0.0269**. That second candidate is a false enemy identity despite a coherent segmented object. A plausible cause is an incorrect Muse box/semantic proposal followed by SAM segmenting the proposed region; mask confidence alone cannot establish the requested identity.

These are the complete candidate masks from these four post-hoc traces, not a balanced detector benchmark. Missing calls, geometric queries and unavailable object-instance labels must not be treated as false negatives. No text-only SAM localization claim is added.

### Navigation and combat decisions

| Initial run | Sampled 128-unit cells | Cell entries / reentries | Translation primitives | Low-displacement collision proxy | Visual-cycle warnings |
| --- | ---: | ---: | ---: | ---: | ---: |
| Basic | 1 | 1 / 0 | 0 | 0 | 0 |
| Arena | 28 | 30 / 2 | 37 | 2 | 9 |
| Corridor | 2 | 2 / 0 | 0 | 0 | 0 |
| Freedoom | 15 | 29 / 14 | 32 | 1 | 1 |

Reentries remove consecutive identical sampled cells before counting returns. These are endpoint samples, not continuous coverage. Corridor's second cell is reached without any translation command; physics/knockback can change position, so it is not evidence of deliberate exploration. Collision counts retain the existing acceleration-sensitive proxy.

Arena meaningfully alternated firing, turning and later retreating; low cell revisits do not establish good resource management or survival. Freedoom revisited sampled cells repeatedly while trying doors/panels. It issued 11 `use` primitives; two were followed by the existing geometry-change proxy, so labeling every interaction ineffective would be inaccurate. Neither the proxy nor a segmented panel verifies its intended function or the correctness of the chosen route. The recorded ledger and replanning notes remained active, but available memory did not prevent repeated interaction attempts or establish exit planning.

### Timestamped demo selections

Timestamps below refer to the **full native-gameplay MP4**, starting at its first recorded frame, at 35 fps. They exclude API waiting time. Event localization is within a native tic (about 0.029 seconds). Original videos and traces remain intact; excerpts are decoded frame trims, re-encoded to H.264 without overlays, speed changes, generated frames or invented outcomes.

| Priority | Full recording | Time range | What it actually shows |
| --- | --- | --- | --- |
| Strongest arena combat | Arena | **00:00.00–00:07.00** | Six recorded kills with firing/turning; no claim of surviving the full arena horizon |
| Strongest initial Freedoom combat | Freedoom initial | **00:12.60–00:16.69** | Reorientation toward enemies, sustained engagement and first kill |
| Second Freedoom kill | Freedoom initial | **00:19.80–00:21.57** | Incoming damage and second kill; exit remains incomplete |
| Grounding-use failure | Arena | **00:07.43–00:09.71** | Correct left-bearing candidate followed by uncorrected, non-damaging bursts |
| Resource failure | Arena | **00:10.69–00:13.49** | Empty pistol, unsuccessful switch attempts in the trace and health collapse |
| Short failure example | Corridor | **00:00.00–00:03.29** | Stationary exchange, one kill and death |

The basic control is only 0.17 seconds of original gameplay, followed by the clearly labeled replay/result hold. It is useful as a controls check, not the main demo. The [compact post-run evidence](m35-demo-audit.json) records the discharge events, mask audits and source/clip hashes. Local excerpts and the full source/clip SHA-256 index are under `results/m35-demo/`; `arena-kills.mp4` and `freedoom-first-engagement.mp4` are the initial recommendations. The extended attempt adds two explicitly labelled reconstructed-prefix excerpts: **00:06.51–00:12.51** (`extended-reconstructed-first-fight.mp4`) and **01:21.49–01:28.51** (`extended-reconstructed-late-fight.mp4`). The latter contains the later two kills and incoming damage. These have verified input/frame/counter provenance, but must be presented as reconstructed-prefix footage because the original recording was not finalized. Optional same-named `.srt` files label native cumulative kill counts; they do not alter any original or recovered MP4.

## Artifacts and reproduction

The [compact evidence](m35-evidence.json) contains per-run measurements, control-use counts, audit mask and frame hashes and artifact hashes. Local run IDs (all UTC) are:

| Scenario | Local run ID |
| --- | --- |
| Basic | `20261104T184348.144630Z_basic_combat_L2_s101` |
| Arena | `20261104T190052.792354Z_defend_center_combat_L2_s101` |
| Corridor | `20261104T190857.601337Z_deadly_corridor_combat_L2_s101` |
| Freedoom | `20261104T191136.406971Z_freedoom2_combat_map01_L2_s101` |

Open `runs/<run_id>/replay.mp4` for each full video. For the longest combat example, start with the arena run; for navigation followed by combat, use Freedoom. The four zero-call startup failures are listed separately in the evidence and excluded from these gameplay counts.

Each finalized local `runs/<run_id>/` folder retains `episode.lmp`, `replay.mp4`, `replay.json`, `manifest.json`, `events.jsonl`, `telemetry.jsonl`, observed JPEG frames, `memory.json` and any grounding masks, overlays and sanitized SAM streaming records. Manifests hash the native recording and logs. Replay independently checks terminal tic/reward and combat counters against telemetry; MP4 exports cover the complete recorded interval, not selected successful moments. Successful exports retain the existing explicitly labeled recap/result hold, separate from gameplay frame counts. There is no fabricated post-terminal gameplay. The synchronous engine pauses during model requests; videos play native gameplay at 35 fps, so their duration is shorter than API-inclusive wall time.

The repository keeps compact derived evidence rather than committing generated videos or full payloads. Run directories and experiment checkpoints remain ignored local artifacts. Private reasoning content and credentials are excluded from the added model-decision log.

To prepare a **new**, unrun plan without API calls:

```bash
uv run --locked python scripts/run_m35.py --out results/m35-new
```

A separately authorized live invocation selects exactly one slot; for example, slot 0 is the basic control:

```bash
uv run --locked python scripts/run_m35.py --out results/m35-new --index 0 --execute
```

The runner exports that run's full replay automatically. To create another export from an existing local run while preserving its original MP4:

```bash
uv run --locked autoqa replay runs/<run_id> --output-name replay-review.mp4
```

Use an actual run ID and a fresh output filename. Replay makes no model calls. Source/config drift or a still-running slot prevents another paid invocation; an interrupted attempt requires reconciliation rather than automatic retry.

## Limits of these experiments

- The extended attempt was interrupted before its approved limits; its original native recording and final manifest/memory were not finalized. Only the separately labelled reconstructed executed prefix is verified. Failed/in-flight request charges remain unknown.
- One seed per scenario and differing objectives do not support completion-rate, comparative-model or causal claims. There is no human baseline and no combat-disabled paired control.
- The basic scenario is a short shooting control. Success there does not establish sustained combat, navigation or unfamiliar-level completion.
- Decision, API-cost and wall-time limits can end an attempt well before its engine horizon. A short recorded interval is not equivalent to surviving the full scenario timeout.
- Simultaneous fire, vertical aim and weapon controls are available and offline-tested. Their availability does not establish effective model use; measured live primitive counts and inventory changes must be consulted.
- SAM returns candidates, not verified identities. A nonempty mask or a navigation note is not proof of correct localization, effective targeting or planning.
- Combat counters are engine measurements. Requested attack duration is not a shot count, and ammunition changes alone do not identify individual hits or resource planning.
- Inline issue candidates retain their existing unverified status. No M4 Reviewer, trace-verdict system or carry-over experiment is implemented here.
