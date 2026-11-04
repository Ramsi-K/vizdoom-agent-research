"""Eval-only, static 128-unit sector graph for telemetry distances."""

import math
from collections import defaultdict, deque


def on_segment(point, a, b):
    x, y = point
    cross = (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])
    return (
        abs(cross) < 1e-7
        and min(a[0], b[0]) - 1e-7 <= x <= max(a[0], b[0]) + 1e-7
        and min(a[1], b[1]) - 1e-7 <= y <= max(a[1], b[1]) + 1e-7
    )


def inside(point, edges):
    x, y = point
    crossings = 0
    for a, b in edges:
        if on_segment(point, a, b):
            return True
        if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
            crossings += 1
    return crossings % 2 == 1


def intersects(a, b, c, d):
    def side(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    return (side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0) or any(
        (on_segment(p, x, y)) for p, x, y in ((a, c, d), (b, c, d), (c, a, b), (d, a, b))
    )


class WalkableTopology:
    """A coarse graph, never proof that a level is reachable or defective."""

    def __init__(self, sectors, goal):
        self.edges = [
            [((line.x1, line.y1), (line.x2, line.y2)) for line in s.lines] for s in sectors
        ]
        self.cells = {}
        self.distances = {}
        if not self.edges:
            return
        all_points = [p for polygon in self.edges for edge in polygon for p in edge]
        owners = defaultdict(set)
        blocked = set()
        for index, sector in enumerate(sectors):
            for line in sector.lines:
                key = tuple(sorted(((line.x1, line.y1), (line.x2, line.y2))))
                owners[key].add(index)
                if line.is_blocking:
                    blocked.add(key)
        closed = {i for i, s in enumerate(sectors) if s.floor_height == s.ceiling_height}
        walls = [edge for edge in blocked if not (len(owners[edge]) > 1 and owners[edge] & closed)]
        for x in range(
            math.floor(min(p[0] for p in all_points) / 128),
            math.floor(max(p[0] for p in all_points) / 128) + 1,
        ):
            for y in range(
                math.floor(min(p[1] for p in all_points) / 128),
                math.floor(max(p[1] for p in all_points) / 128) + 1,
            ):
                center = ((x + 0.5) * 128, (y + 0.5) * 128)
                sector_id = self.sector_at(center)
                if sector_id is not None:
                    self.cells[x, y] = center
        target = (math.floor(goal[0] / 128), math.floor(goal[1] / 128)) if goal else None
        if target not in self.cells:
            return
        self.distances[target] = 0
        queue = deque([target])
        while queue:
            cell = queue.popleft()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                neighbor = (cell[0] + dx, cell[1] + dy)
                if neighbor not in self.cells or neighbor in self.distances:
                    continue
                if any(intersects(self.cells[cell], self.cells[neighbor], *wall) for wall in walls):
                    continue
                self.distances[neighbor] = self.distances[cell] + 128
                queue.append(neighbor)

    def sector_at(self, point):
        return next((i for i, edges in enumerate(self.edges) if inside(point, edges)), None)

    def distance(self, point):
        return self.distances.get((math.floor(point[0] / 128), math.floor(point[1] / 128)))
