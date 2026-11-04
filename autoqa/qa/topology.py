"""Evaluation-only coarse branch evidence; never a dynamic softlock oracle."""

import math

from autoqa.doom.topology import intersects
from autoqa.eval.metrics import number, topology_from_geometry


def branch_observation(trace, points):
    if any(not all(number(row.get(key)) for key in ("pos_x", "pos_y", "tic")) for row in points):
        return None
    goals = [o for o in trace.geometry.get("objects", []) if o.get("name") == "GreenArmor"]
    if len(goals) != 1 or not trace.geometry.get("sectors") or not points:
        return None
    goal = (goals[0]["x"], goals[0]["y"])
    topology = topology_from_geometry(trace.geometry, goal)
    # Restrict to the measured goal-connected component. Missing cells stay unknown.
    cells = topology.distances
    closed = any(s.get("floor") == s.get("ceiling") for s in trace.geometry["sectors"])
    if closed:
        return None  # Initial closed-door snapshots are insufficient dynamic evidence.
    walls = {
        tuple(sorted(((line["x1"], line["y1"]), (line["x2"], line["y2"]))))
        for s in trace.geometry["sectors"]
        for line in s["lines"]
        if line.get("blocking")
    }
    adjacency = {}
    for cell in cells:
        adjacency[cell] = []
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            neighbor = (cell[0] + dx, cell[1] + dy)
            if neighbor in cells and not any(
                intersects(topology.cells[cell], topology.cells[neighbor], *wall) for wall in walls
            ):
                adjacency[cell].append(neighbor)
    target = (math.floor(goal[0] / 128), math.floor(goal[1] / 128))
    branches = []
    for leaf, neighbors in adjacency.items():
        if len(neighbors) != 1 or leaf == target:
            continue
        branch = {leaf}
        previous, current = leaf, neighbors[0]
        while len(adjacency[current]) == 2 and current != target:
            branch.add(current)
            onward = [n for n in adjacency[current] if n != previous]
            previous, current = current, onward[0]
        if target not in branch:
            branches.append(branch)
    sustained = 0
    for branch in branches:
        since = None
        last = None
        for row in points:
            key = (math.floor(row["pos_x"] / 128), math.floor(row["pos_y"] / 128))
            tic = trace.tic(row["tic"])
            if key not in branch or tic is None:
                since = last = None
                continue
            # Gaps over 35 tics do not demonstrate continuous sampled occupancy.
            if since is None or (last is not None and tic - last > 35):
                since = tic
            last = tic
            sustained = max(sustained, tic - since)
    keys = [(math.floor(r["pos_x"] / 128), math.floor(r["pos_y"] / 128)) for r in points]
    measured = all(key in cells for key in keys)
    monotonic = measured and all(cells[a] >= cells[b] for a, b in zip(keys, keys[1:]))
    reduction = cells[keys[0]] - cells[keys[-1]] if measured else None
    return dict(
        coarse_goal_free_branch_dwell_tics=sustained,
        measured_all_cells=measured,
        monotonic_goal_progress=monotonic,
        coarse_bfs_reduction=reduction,
        static_goal_connected_component_size=len(cells),
        dynamic_reachability_verified=False,
    )
