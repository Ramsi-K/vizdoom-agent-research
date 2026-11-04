"""Eval-only engine reads. record_step deliberately returns None."""

import math

import numpy as np
import vizdoom as vzd

from autoqa.io import EventLog, write_json

from .topology import WalkableTopology


class TelemetryRecorder:
    def __init__(self, directory, calibration, *, combat=False, track_armor_goal=True):
        self.log = EventLog(directory / "telemetry.jsonl")
        self.directory = directory
        self.calibration = calibration
        self.last = None
        self.goal = None
        self.previous_heights = None
        self.topology = None
        self.combat = combat
        self.track_armor_goal = track_armor_goal

    def record_step(self, game, event, decision_idx, primitive_idx=None, move=None, run=False):
        state = game.get_state()
        values = {
            name: game.get_game_variable(getattr(vzd.GameVariable, variable))
            for name, variable in {
                "pos_x": "POSITION_X",
                "pos_y": "POSITION_Y",
                "pos_z": "POSITION_Z",
                "angle_deg": "ANGLE",
                "health": "HEALTH",
                "armor": "ARMOR",
                "camera_fov": "CAMERA_FOV",
            }.items()
        }
        objects = state.objects if state else []
        if self.combat:
            for key, variable in {
                "kills": "KILLCOUNT",
                "items": "ITEMCOUNT",
                "hits": "HITCOUNT",
                "damage_dealt": "DAMAGECOUNT",
                "damage_taken": "DAMAGE_TAKEN",
                "selected_weapon": "SELECTED_WEAPON",
                "selected_weapon_ammo": "SELECTED_WEAPON_AMMO",
                "pitch": "PITCH",
                **{f"ammo_{i}": f"AMMO{i}" for i in range(10)},
                **{f"weapon_{i}": f"WEAPON{i}" for i in range(10)},
            }.items():
                values[key] = game.get_game_variable(getattr(vzd.GameVariable, variable))
        if self.goal is None and self.track_armor_goal:
            goal = next((o for o in objects if o.name == "GreenArmor"), None)
            if goal:
                self.goal = (goal.position_x, goal.position_y)
        sectors = state.sectors if state else []
        heights = [(s.floor_height, s.ceiling_height) for s in sectors]
        if event == "init":
            self.topology = WalkableTopology(sectors, self.goal)
            write_json(
                self.directory / "geometry.json",
                {
                    "sectors": [
                        dict(
                            floor=s.floor_height,
                            ceiling=s.ceiling_height,
                            lines=[
                                dict(
                                    x1=line.x1,
                                    y1=line.y1,
                                    x2=line.x2,
                                    y2=line.y2,
                                    blocking=line.is_blocking,
                                )
                                for line in s.lines
                            ],
                        )
                        for s in sectors
                    ],
                    "objects": [
                        dict(name=o.name, x=o.position_x, y=o.position_y, z=o.position_z)
                        for o in objects
                    ],
                },
            )
        displacement = (
            math.dist([values["pos_x"], values["pos_y"]], [self.last["pos_x"], self.last["pos_y"]])
            if self.last
            else 0.0
        )
        translation = move and move.type in {"forward", "backward", "strafe_left", "strafe_right"}
        speed = self.calibration["forward_units_per_tic"][
            "run" if run and move and move.type in {"forward", "backward"} else "walk"
        ]
        expected = speed * move.amount if translation else None
        labels = (
            [label for label in state.labels if label.object_name == "GreenArmor"]
            if state and self.track_armor_goal
            else []
        )
        reward = game.get_total_reward()
        record = dict(
            event=event,
            decision_idx=decision_idx,
            primitive_idx=primitive_idx,
            tic=game.get_episode_time(),
            **values,
            cell_x=math.floor(values["pos_x"] / 128),
            cell_y=math.floor(values["pos_y"] / 128),
            reward_total=reward,
            reward_delta=reward - (self.last["reward_total"] if self.last else 0),
            displacement_units=displacement,
            expected_free_displacement=expected,
            displacement_estimate_method="M0 eighth-tic proxy; includes acceleration; not steady-state",
            collision_substep=displacement < 0.20 * expected if expected else False,
            goal_dist_euclidean=math.dist([values["pos_x"], values["pos_y"]], self.goal)
            if self.goal
            else None,
            goal_dist_bfs=self.topology.distance((values["pos_x"], values["pos_y"]))
            if self.topology and self.topology.edges
            else (math.dist((values["pos_x"], values["pos_y"]), self.goal) if self.goal else None),
            goal_dist_bfs_status="measured_coarse_grid"
            if self.topology
            and self.topology.distance((values["pos_x"], values["pos_y"])) is not None
            else "unresolved_on_coarse_grid"
            if self.topology and self.topology.edges
            else "euclidean_fallback_no_sectors",
            sector_id=self.topology.sector_at((values["pos_x"], values["pos_y"]))
            if self.topology
            else None,
            goal_visible_pixels=int(
                sum(np.count_nonzero(state.labels_buffer == label.value) for label in labels)
            )
            if state
            else None,
            goal_bbox=[labels[0].x, labels[0].y, labels[0].width, labels[0].height]
            if labels
            else None,
            sector_heights=heights if state else None,
            door_or_switch_activated=bool(
                move
                and move.type == "use"
                and self.previous_heights is not None
                and heights
                and heights != self.previous_heights
            ),
        )
        self.log(event, **{key: value for key, value in record.items() if key != "event"})
        self.last = record
        if heights:
            self.previous_heights = heights
