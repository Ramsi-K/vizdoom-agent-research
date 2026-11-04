# M1 measured validation

M1 implements the pixel-only Muse Spark Pilot, four validated tools (`act`, `note`, `report_issue`, `finish`), bounded guards, eval-only telemetry, native `.lmp` recording, and H.264 replay. SAM is absent from this runtime; independent text-guided grounding remains unverified and belongs to M2. No M2–M5 feature or project specification was changed.

## Executed episodes

Both runs used `my_way_home`, Level 1, seed 101, `muse-spark-1.3`, effort `low`, 60 decisions, 2,100 tics, and USD 0.25 as the configured cost threshold. The first run preceded final telemetry work; the second ran the committed implementation. Both are retained, with no selection based on gameplay outcome.

| Stage | Outcome | Act calls | API calls | Tics used | Final tic | Reward | Estimated USD | Wall seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| initial runtime validation before final telemetry | max_decisions | 60 | 62 | 831 | 841 | -0.08310000 | 0.248436 | 189.48 |
| final committed implementation validation | budget_exhausted | 57 | 66 | 750 | 760 | -0.07500000 | 0.253330 | 214.63 |

Neither run completed the level. The first rejected one tool response; the final run rejected seven. In particular, the first run received `run="False"` as a string instead of a JSON boolean and rejected it without execution. The final run matched all 68 executed primitives to model act arguments, with zero unmatched primitives and zero API errors. Detailed per-run audits are retained in the evidence file.

## Acceptance evidence

- `make test`: 54 passed; four live API tests deselected, 5.63 seconds. The required `test_info_barrier.py`, `test_no_scripted_policy.py`, `test_responses_replay.py`, and `test_determinism_and_replay.py` pass. `make lint` and `git diff --check` pass.
- Real runs completed through the Muse Spark loop. The action audit matches each executed primitive to the corresponding validated model `act` call. No scripted fallback action was inserted. Runtime imports do not reach M0 calibration scripts.
- Stateless tool follow-ups preserve encrypted reasoning, commentary phase, function calls, and matching tool outputs. Usage and latency are logged for every response; cached tokens and reasoning tokens remain separate in the usage summary.
- Strict schemas reject invalid primitives and tool arguments without clamping them. Guards stop at budgets or add text warnings; they do not choose actions.
- `note`, `report_issue`, and `finish` are covered by offline loop tests. Inline issue reports stay explicitly unverified; no M4 verifier or defect claim is included.

## Native replay

Both real runs were rendered with `autoqa replay <run_dir> --size 1280x720`. Each MP4 is readable H.264 with faststart, and matches the final telemetry tic exactly and reward within 1e-8. Caption rendering is covered by the deterministic offline replay test.

| Run | Encoded frames | Dimensions | Replay final tic | Replay reward | Matches telemetry |
| --- | --- | --- | --- | --- | --- |
| 20261104T071718.181447Z_my_way_home_L1_s101 | 831 | [720, 1280, 3] | 841 | -0.08310000 | True |
| 20261104T072627.517704Z_my_way_home_L1_s101 | 750 | [720, 1280, 3] | 760 | -0.07500000 | True |

## Timing and interpretation limits

- The bundled `my_way_home.cfg` starts at engine tic 10. Forcing tic 1 leaves early controls ineffective during scenario startup. The implementation preserves the bundled startup delay, records raw tic 10 honestly, and exposes elapsed tic 0 to the Pilot. This differs from the specification's literal raw `tic=1` wording; the specification is unchanged.
- A native demo flushed before natural termination contains a trailing tic. Replay sets its timeout to the logged action interval before `init()` and then uses only `advance_action(1, True)`. This avoids replaying a trailing tick that was not part of the original action trace.
- The final telemetry includes exact sector IDs and a static 128-unit graph. A disconnected or unrepresented coarse-grid cell yields `goal_dist_bfs=null` plus `unresolved_on_coarse_grid`; it is not treated as evidence of a level defect. A no-sector case uses the specified Euclidean fallback.
- Collision estimates use the M0 eighth-tic movement proxy, which includes acceleration and is not a steady-state model. Raw displacement and calibration provenance are retained.
- Cost is calculated from API-reported token usage and the explicit Meta price table, not a billing receipt. Budgets are checked after each response, so an in-flight response can cross the configured threshold; no returned action executes after that check fails.
- These are engineering validation runs, not a gameplay-completion benchmark, information-level comparison, or human playability measurement. A completed harness execution does not mean the agent completed the level.
- M1 supports only `my_way_home` at Level 1. SAM grounding, L2/L3 briefings/maps, sweeps, metrics/reporting, defect verification, and carry-over are not implemented.

## Files and reproducibility

Full sanitized measurements, action audits, usage, source code hashes, native replay checks, and artifact hashes are in [m1-evidence.json](m1-evidence.json). Raw episodes, frames and videos remain ignored under `runs/`; no API keys, `.env`, HTTP headers, encrypted reasoning payloads or raw run folders are committed.

```bash
uv sync --locked
make test
make lint
uv run autoqa run --scenario my_way_home --level 1 --seed 101
uv run autoqa replay runs/<run_id> --size 1280x720
```

`MODEL_API_KEY` or the existing `META_MODEL_API_KEY` alias is read from the environment or ignored `.env`. Replay outputs are preserved and are not overwritten.

The existing local edits to `docs/02-agent.md` were backed up, preserved byte for byte across branch synchronization, and excluded from the M1 commits. Local `main` was fast-forwarded to `origin/main` before creating `feat/m1-pilot`.
