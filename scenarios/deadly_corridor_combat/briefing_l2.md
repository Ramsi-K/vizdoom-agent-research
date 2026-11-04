## Objective
Reach the armor objective alive before the episode timeout in the bundled deadly-corridor environment. Enemies are enabled at difficulty 3. Positive shaping reward during movement is not proof of completion.

## Mechanics
ATTACK fires the current weapon. Weapon keys request an owned weapon. Walking over pickups collects them. Use interacts with nearby doors and switches when facing them. The HUD provides visible health and ammunition.

## Failure conditions
Player death, scenario timeout, or a harness limit before reaching the objective.

## Exclusions
No route, enemy coordinates, aiming direction, combat tactics, or target priorities are supplied.
