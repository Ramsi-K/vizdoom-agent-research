from types import SimpleNamespace

from autoqa.doom.topology import WalkableTopology, inside


def rectangle(left, right, *, block_middle=False):
    points = [(left, 0), (right, 0), (right, 128), (left, 128)]
    lines = [
        SimpleNamespace(x1=a[0], y1=a[1], x2=b[0], y2=b[1], is_blocking=True)
        for a, b in zip(points, points[1:] + points[:1], strict=True)
    ]
    if block_middle:
        lines.append(SimpleNamespace(x1=128, y1=0, x2=128, y2=128, is_blocking=True))
    return SimpleNamespace(floor_height=0, ceiling_height=128, lines=lines)


def test_sector_graph_distance_and_wall_separation():
    graph = WalkableTopology([rectangle(0, 384)], (320, 64))
    assert graph.distance((64, 64)) == 256
    assert graph.sector_at((64, 64)) == 0
    blocked = WalkableTopology([rectangle(0, 128), rectangle(128, 384)], (320, 64))
    assert (0, 0) in blocked.cells
    assert blocked.distance((64, 64)) is None
    assert WalkableTopology([], None).distance((0, 0)) is None


def test_point_in_sector_handles_unordered_edges():
    sector = rectangle(0, 128)
    edges = [((line.x1, line.y1), (line.x2, line.y2)) for line in reversed(sector.lines)]
    assert inside((64, 64), edges)
    assert not inside((-20, 64), edges)


def test_closed_two_sided_door_is_traversable_in_global_topology():
    door = rectangle(128, 256)
    door.ceiling_height = door.floor_height
    graph = WalkableTopology([rectangle(0, 128), door, rectangle(256, 384)], (320, 64))
    assert graph.distance((64, 64)) == 256
