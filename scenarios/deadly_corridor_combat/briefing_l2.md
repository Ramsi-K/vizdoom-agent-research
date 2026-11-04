## Objective
Reach the armor objective alive before the episode timeout in the bundled deadly-corridor environment. Enemies are enabled at difficulty 3. Positive shaping reward during movement is not proof of completion.
Completion means touching the armor while alive. Eliminating every enemy is not a completion requirement; choose how combat and movement serve the objective yourself.

## Mechanics
ATTACK fires the current weapon. Weapon keys request an owned weapon. Walking over pickups collects them. Use interacts with nearby doors and switches when facing them. The HUD provides visible health and ammunition.
Read the current HUD afresh when updating health or ammunition in memory. The glyph after health is a percent sign, not another digit: a single-digit health value must not become a two-digit number. If a value is unreadable, record it as uncertain rather than carrying forward or inventing a precise value.

## Failure conditions
Player death, scenario timeout, or a harness limit before reaching the objective.

## Exclusions
No route, enemy coordinates, aiming direction, combat tactics, or target priorities are supplied.
