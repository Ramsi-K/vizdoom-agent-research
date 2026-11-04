# M2: grounding-guided L2 and explicit map planning in L3

2026-11-04. Continued on `feat/m2-grounder`, draft PR #10. **One L3 retest completed `my_way_home`; L2 regressed on cost and coarse exploration and did not win.** This is one stochastic paired seed, not a completion-rate or causal-benefit estimate. M2 remains partial on grounding reliability and other information-level/scenario acceptance checks.

## Changes and boundaries

The existing landmark/outcome ledger, 1,500-character scratchpad, 20-decision history and visual-cycle guard are retained. No second navigation memory, navigation model, pathfinder or action policy was added.

L2 and L3 append level-specific instructions to the existing v3 prompt. L1's prompt, tool definitions and memory behavior remain unchanged; regression tests compare their hashes against the pre-change implementation. L1 was not rerun or tuned.

A guided-level model writes a compact `NAV {...}` JSON line through the existing `note` tool, alongside its landmark/outcome text. It records current decision, tentative qualitative location/heading, destination, up to three subgoals, grounding references, uncertainty, replanning reason and next visual check. It expires after five decisions and must be refreshed on a visual-cycle warning. The harness rejects an act lacking a valid current annotation; it never chooses a movement or supplies a route. All annotation content comes from Muse.

- **L2:** prompt asks Muse to inspect a visible target with `locate`, use candidate geometry/bearing to choose or reject a target, and compare subsequent screenshots with its predicted outcome. Failed localization is valid evidence of uncertainty, not object absence. Old bearings are explicitly stale after motion. Reference IDs tie updates to actual results returned on earlier tool rounds; a model cannot claim to have used a result produced in the same response. Identical concept lookups within the same observed frame reuse their result. Different frames are never silently reused.
- **L3:** map is the primary source of strategy: interpret S/G, propose a qualitative route and subgoals, retain tentative location/heading, and replan against contradictions. Screenshots guide immediate motion/obstacle avoidance. Unknown location is permitted; current coordinates are never supplied. L3 also permits optional grounding, with the same uncertainty rules.
- **Telemetry:** `navigation_update` records model-written plans and their actual grounding evidence; `navigation_action` links each act to its active plan and model-written intent. Both explicitly retain `planning_verified=false`. Their counts establish declarations and provenance, not effective planning. Raw notes/outcomes and native trajectories provide the separate behavioral evidence.

Runtime AI remains Muse Spark/SAM only. New guidance never reads engine pose, labels or topology. Static L3 map delivery remains the original once-only initial snapshot; there is no GPS or live marker. Model actions remain unchanged when executed. Failed/invalid navigation metadata produces a tool error, never a fallback action.

## Controlled protocol

Compared against the latest ledger/cycle baselines:

- L2 baseline: `20261104T092538.514056Z_my_way_home_L2_s101`.
- L3 baseline: `20261104T092542.608122Z_my_way_home_L3_s101`.

Both fresh retests use seed **101**, `my_way_home`, Muse Spark 1.3 / low effort, experimental `muse_box_sam`, **60-decision maximum**, 2,100-tic maximum, **USD 0.60 stop threshold**, 900 wall-clock seconds, and the same request/output limits. Baseline and retest config dictionaries, spawn, briefing hashes and frozen map are equal. Runtime/prompt changes were frozen at local checkpoint `15d2db0` before the live requests. No prompt tuning or extra gameplay runs followed the results.

| Measurement | L2 baseline → current | L3 baseline → current |
|---|---:|---:|
| Outcome | max decisions → max decisions | max decisions → **success** |
| Decisions / executed act calls | 60/60 → 60/59 | 60/60 → **42/42** |
| Played tics | 1,033 → 732 | 1,236 → **1,280** (native replay count) |
| Unique sampled 128-unit cells | 9 → **6** | 14 → **18** |
| Unique sampled 64-unit cells | 18 → 18 | 29 → 29 |
| Known-cell reentries / all cell entries | 18/27 → **16/22** | 30/44 → **15/33** |
| Near-repeated poses (≤24 units, ≤15°, ≥6 decisions apart) | 7 → 1 | 8 → 0 |
| Translation collision proxy / translations | 5/50 → 6/48 | 1/49 → 3/48 |
| Best Euclidean goal distance | 400.1 → 340.2 | 334.3 → **35.0 (pickup)** |
| Final Euclidean goal distance | 400.1 → **475.7** | 656.9 → **35.0 (pickup)** |
| Goal pixels ever observed | no → no | no → **yes, max 6,317** |
| Total recorded USD | 0.36663055 → **0.58523915** | 0.31053015 → **0.29667895** |

All grid/path measures use recorded endpoints, not continuous coverage; path chords can cross walls. Euclidean distance ignores walls and does not measure a navigable route. Coarse BFS remains unresolved. The collision flag is the pre-existing acceleration-sensitive displacement proxy, not an exact engine collision event. Pickup occurs within engine contact range, so success does not require Euclidean distance zero. L3 terminated early after success; its counts cover 42 decisions rather than the full 60-decision cap. Its last 20 actual decisions (22–41) added 11 coarse cells; L2's decisions 40–59 added one.

## Was the information actually used?

### L2: citations and target selection are observable; effective guidance remains unverified

There were **five locate calls**, seven grounding model requests, two candidate-positive results, no exact-frame duplicate reuses, **51 navigation updates**, and **59 plan-linked acts**. Grounding cost was USD 0.01709825 (included in total). Only **two acts** declared grounding as their basis; **57** declared visual navigation after inconclusive/stale grounding. Merely attaching the earlier receipt to a visual intention is not counted as verified grounding-guided movement.

At decision 0, `gray door` returned an unverified candidate at bearing **−22.263°**, combined confidence 0.85. Muse's note cited that receipt, selected the candidate as its destination, stated that identity/passability were unknown and predicted that it would grow in view. Its first action moved forward; it did not initially align by the suggested bearing. At decision 1, Muse described a forward overshoot, explicitly treated the old bearing as stale and chose to turn left using pixels. This is observable target selection and subsequent revision, but not successful semantic identification or a validated bearing controller.

At decision 20 another door candidate was returned. `green armor` lookups at decisions 24 and 26, and `gray cabinets` at 46, returned no candidate. Their uncertainty persisted in the existing history/notes. No detection is promoted to a verified identity. Repeated armor lookups were on different frames, not cache hits; the result does not establish that all localization calls were useful.

**Regression:** cost increased by approximately **59.6%**, coarse exploration decreased 9→6, and the coarse reentry fraction increased about 66.7%→72.7%. Four invalid/stale navigation annotations were rejected. Decision 46 used its four tool rounds without an act, so only 59 actions executed under the unchanged 60-decision limit. No raw action schema response was rejected. Added bookkeeping and an unreliable initial target hypothesis did not deliver a win. Lower pose recurrence and a transiently smaller straight-line goal distance do not reverse that conclusion.

### L3: explicit map intentions precede route execution and a real pickup

There were **37 model-written navigation updates**, **42 linked acts**, four visual-cycle warnings and **zero locate calls**. Every act declared map strategy, but this is a protocol requirement and is **not** itself evidence of effectiveness.

The stronger evidence is the content and sequence:

1. Before moving, Muse identified its destination as **“G east-south room”**, with subgoals to leave the tentative S region, traverse the north/east connection and go south to G. This direction/route information is consistent with the supplied map and is absent from the L2 briefing. The goal had no visible pixels at this point.
2. Localization/execution were initially poor. At decision 13 Muse still described red pillars “near S,” although evaluation-only telemetry placed it roughly 350 units from spawn in the southern branch. It spent much of its first 26 decisions making uncertain escape/reorientation attempts. Those incorrect beliefs are retained rather than counted as successful localization.
3. Later intentions continued to reference the east corridor and southern goal room. Recorded positions show eastward traversal from approximately `(704,-108)` at decision 33 to `(1005,26)` at 36, then south toward `(1001,-205)` at 39. These coordinates are used only in this offline evaluation; they were not sent to Muse.
4. Goal pixels appeared late. At decision 40 the model updated its map hypothesis to the southeast goal room, with a short alignment/pickup subgoal. At 41 it chose a forward move. Native telemetry reports armor **100**, positive reward **0.872**, and natural episode completion. The `.lmp` independently reproduces the pickup.

This supports **observed explicit map planning followed by successful execution in this episode**. It does not prove every action used the map, accurate localization throughout, that the map caused the improvement, or reliable completion on another run. There is no map-withheld ablation or multi-seed sweep in M2.

## Replay and terminal-clock finding

The first natural win exposed an existing clock assumption: ViZDoom resets both episode and server clock to zero on completion. The original winning manifest therefore contains `final_tic=0` and the old code's invalid `tics_used=-10`. **Neither manifest nor telemetry was rewritten.** A read-only native probe observed raw tic 1289 before the final step and zero afterward; 1,280 `advance_action(1)` calls elapsed from initial tic 10.

Replay now supports this observed success/reset case. It preserves raw clock/reward comparisons and separately records `reconstructed_tics_used=1280`, `reconstructed_final_tic=1290`, and the reconstruction source. The 1280×720 H.264 replay matches the recorded raw terminal state and reward. Future run manifests mark a reset clock as needing replay and use `null` for unavailable elapsed tics rather than a negative number. These post-run changes affect reporting/replay only; planning/gameplay files match the measured runtime. The evidence lists the two source-hash differences explicitly. No extra AI run was used to hide the bug.

L2's H.264 replay matches raw tic 742 and reward −0.0732 normally. All **171** executed primitives (93 L2, 78 L3) match model act requests. The winning final primitive's raw clock regression is reconciled only from native replay: it executed 15 of its requested 20 tics before completion. No scripted game action or privileged route was introduced.

## Tests, cost and PR cleanup

**92 tests passed**, four M0 live tests deselected, in **7.23 s**; lint/format pass. Added tests cover L1 prompt/tool preservation, actual earlier grounding citations, forged/future/stale plans, level restrictions, exact-frame reuse, plan/action linkage without declaring planning verified, and native-terminal reset replay without rewriting telemetry. Existing ledger, cycle, information-barrier and native determinism tests pass.

Each retest had one recovered `APITimeoutError`; no grounding error occurred. Total returned-usage cost is **USD 0.88191810**; any provider charge for the two timed-out attempts is unknown. Cumulative recorded M2 cost is **USD 3.61586140**, excluding unknown timeout charges. Caching after the first response was nonzero on 115/118 L2 and 78/78 L3 responses. A response can cross a USD threshold before usage is known, as previously documented; neither current run crossed its threshold.

The original 55-file / 24,075-addition PR was dominated by repeated manifests, full decision traces and ledger snapshots. Full intermediate records are archived in ignored `runs/m2-navigation-review/` and remain accessible at immutable pre-cleanup commit `5e157ad`. The final PR consolidates compact historical/current measurements into [m2-evidence.json](m2-evidence.json), retains all 56 paired grounding cases in [m2-grounding-cases.csv](m2-grounding-cases.csv), and all 88 new model-written navigation updates in [m2-navigation-intentions.csv](m2-navigation-intentions.csv). Full traces, recordings, replay strips and generated trajectory images are excluded from the final PR tree. Required ten real test JPEGs and SAM wire/geometry fixtures remain; JSON fixture compaction changes no values. Unused historical `system_v2.md` is excluded; active L1 `system_v3.md` is unchanged. History is not rewritten. The final diff remains 55 files, including the ten required frame JPEGs, but is approximately 5,200 additions rather than 24,075 (about 78% fewer). The largest changed file is the 76,903-byte consolidated evidence JSON; no changed file exceeds 77 KB.

The existing uncommitted `docs/02-agent.md` edit is preserved byte for byte and excluded. No project specifications, README, M3–M5 features or non-Meta runtime models were changed. The PR remains draft and unmerged.

Reproduce the controlled run independently for each level:

```bash
UV_CACHE_DIR=/tmp/autoqa-uv-cache make test
UV_CACHE_DIR=/tmp/autoqa-uv-cache make lint
uv run --locked autoqa run --scenario my_way_home --level 2 --seed 101 \
  --max-decisions 60 --max-usd 0.60 --grounding-mode muse_box_sam
# Repeat with --level 3, then inspect/replay both run directories:
uv run --locked autoqa replay <run-directory>
uv run --locked python scripts/inspect_m2_trajectories.py <L2-run> <L3-run> \
  --out <local-trajectory-audit.json>
```

Raw local run IDs: `20261104T101158.275076Z_my_way_home_L2_s101` and `20261104T101202.353976Z_my_way_home_L3_s101`. Successful outcomes must be verified separately from declared intentions. Grounding reliability, repeated-run map effectiveness and the unannotated Freedoom exit remain open M2 limitations.

## Demo replay ending

Successful full replays now keep the recorded gameplay, replay its final second at 0.5× speed, and add a three-second labeled result hold. The hold uses a captured approach frame and the replay-matched terminal result; it does not fabricate post-win gameplay or repaint the HUD. Gameplay and presentation frame counts are recorded separately. Sliced clips and `--no-outcome-ending` remain gameplay-only, and `--output-name` preserves existing exports.

The winning run's `replay-demo.mp4` is 41.57 seconds: 1,280 recorded gameplay frames plus 175 presentation frames. Original `replay.mp4`, its metadata, the native demo and raw telemetry remain unchanged. The new MP4 remains local under ignored `runs/`, not in the PR. This fixes the abrupt presentation cut at the pickup without changing the experiment or spending more API credits.
