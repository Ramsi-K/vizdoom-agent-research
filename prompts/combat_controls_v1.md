Combat control extension (available only in this experiment):
Every primitive in act.moves requires a boolean fire. fire=true holds ATTACK simultaneously with that primitive for its duration; fire=false adds no attack. The attack primitive itself still holds ATTACK. This exposes simultaneous firing and movement/aiming; choose all actions yourself.

Additional primitives and units:
- look_up, look_down: amount 1–45 degrees, applied in one tic. Engine view limits may restrict the resulting pitch.
- weapon_1 through weapon_7: amount=1, presses the corresponding weapon-slot key for one tic. Ownership/ammunition determine whether it changes weapons; no weapon is granted by the harness.
- next_weapon, previous_weapon: amount=1, one-tic weapon-cycle key press.
- jump, crouch, altattack: amount 1–35 tics, holds the corresponding engine control. The map/game may not support an effect.
- reload: amount=1, one-tic engine reload key; conventional Doom weapons may ignore it.

The existing movement, turn, use, attack, wait and run units and the four-primitive/70-tic act limit are unchanged. A fire modifier does not add game time. Only validated model act calls advance gameplay. You receive screenshots/HUD and your existing memory; enemy coordinates, ammo counters and other evaluation telemetry are not supplied separately. Determine goals/interactions from your permitted information and choose your own approach. No combat strategy or target priority is prescribed.
