"""Ground-truth-labelled calibration fixtures. Never imported by gameplay."""

import argparse
import json
import math
from pathlib import Path

import numpy as np
import vizdoom as vzd
from PIL import Image

from autoqa.config import RunConfig
from autoqa.doom.env import configured_game, jpeg
from autoqa.io import digest, write_json

CONCEPTS = {
    "GreenArmor": "green armor",
    "Medikit": "medkit",
    "CustomMedikit": "medkit",
    "Clip": "ammo clip",
    "HealthBonus": "health bonus",
    "ArmorBonus": "armor bonus",
    "Shotgun": "shotgun",
}


def capture(directory):
    directory.mkdir(parents=True, exist_ok=True)
    rows = []
    bearing_rows = []
    for scenario in ("my_way_home", "health_gathering_supreme", "freedoom2_map01"):
        game = configured_game(RunConfig(scenario=scenario))
        try:
            game.init()
            # Calibration-only movement/turning; never a gameplay result.
            if scenario == "my_way_home":
                for episode in range(32):
                    state = game.get_state()
                    target = next(o for o in state.objects if o.name == "GreenArmor")
                    x, y, yaw = [
                        game.get_game_variable(v)
                        for v in (
                            vzd.GameVariable.POSITION_X,
                            vzd.GameVariable.POSITION_Y,
                            vzd.GameVariable.ANGLE,
                        )
                    ]
                    angle = math.degrees(math.atan2(target.position_y - y, target.position_x - x))
                    delta = (angle - yaw + 180) % 360 - 180
                    game.make_action([0] * 9 + [-delta], 1)
                    if any(label.object_name == "GreenArmor" for label in game.get_state().labels):
                        break
                    game.new_episode()
            count = 0
            for step in range(160 if scenario == "freedoom2_map01" else 12):
                if game.is_episode_finished():
                    break
                state = game.get_state()
                valid = [
                    label
                    for label in state.labels
                    if label.object_name in CONCEPTS and label.width * label.height >= 16
                ]
                if valid and count < 7:
                    path = directory / f"{scenario}_{count:02d}.jpg"
                    path.write_bytes(jpeg(state.screen_buffer))
                    x, y, yaw, fov = [
                        game.get_game_variable(v)
                        for v in (
                            vzd.GameVariable.POSITION_X,
                            vzd.GameVariable.POSITION_Y,
                            vzd.GameVariable.ANGLE,
                            vzd.GameVariable.CAMERA_FOV,
                        )
                    ]
                    labels = []
                    for label in valid:
                        mask = (state.labels_buffer == label.value).astype(np.uint8)
                        mask_path = path.with_name(path.stem + f"_{label.value}.png")
                        Image.fromarray(mask * 255).save(mask_path)
                        obj = next((o for o in state.objects if o.id == label.object_id), None)
                        if obj is None:
                            continue
                        box = [label.x, label.y, label.x + label.width, label.y + label.height]
                        angle = math.degrees(math.atan2(obj.position_y - y, obj.position_x - x))
                        gt = -((angle - yaw + 180) % 360 - 180)
                        item = dict(
                            name=label.object_name,
                            concept=CONCEPTS[label.object_name],
                            box=box,
                            mask=str(mask_path),
                            gt_bearing=gt,
                            obj_x=obj.position_x,
                            obj_y=obj.position_y,
                        )
                        labels.append(item)
                        if box[0] > 0 and box[2] < 640 and abs(gt) < 40:
                            bearing_rows.append(
                                dict(
                                    frame=str(path),
                                    scenario=scenario,
                                    pos_x=x,
                                    pos_y=y,
                                    angle_deg=yaw,
                                    camera_fov=fov,
                                    **item,
                                )
                            )
                    rows.append(
                        dict(
                            path=str(path),
                            scenario=scenario,
                            labels=labels,
                            tic=game.get_episode_time(),
                            pose=[x, y, yaw],
                            frame_sha256=digest(path),
                        )
                    )
                    count += 1
                if count >= 7:
                    break
                vector = [0.0] * 10
                if scenario == "freedoom2_map01":
                    # Fixed diagnostic trace explores views without a model.
                    if step % 10 < 6:
                        vector[0] = 1
                        game.make_action(vector, 10)
                    elif step % 10 == 6:
                        vector[5] = 1
                        game.make_action(vector, 1)
                    elif step % 10 == 7:
                        game.make_action(vector, 10)
                    else:
                        vector[9] = 45
                        game.make_action(vector, 1)
                else:
                    vector[9] = 5 if scenario == "my_way_home" else 30
                    game.make_action(vector, 1)
        finally:
            game.close()
    write_json(
        directory / "fixtures.json",
        dict(
            frames=rows,
            bearings=bearing_rows,
            selection="Positive visible actor fixtures from fixed calibration traces, before model evaluation; no gameplay-completion claims",
        ),
    )
    print(
        json.dumps(
            dict(
                frames=len(rows),
                bearing_records=len(bearing_rows),
                scenarios=sorted({r["scenario"] for r in rows}),
            )
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    capture(parser.parse_args().out)
