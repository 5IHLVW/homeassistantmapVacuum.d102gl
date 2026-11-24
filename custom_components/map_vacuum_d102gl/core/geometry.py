"""Vector geometry of the Xiaomi JSON map (d102gl and similar).

Turns the pixel grid of the map payload (the same data the parser renders into
the PNG) into room / wall / free-floor outlines as polygons in robot
coordinates (mm, y axis up), plus the cleaning path. It also finds the room
under the robot by pixel and exposes the raw yaw values.

Pure Python; a ~230x190 grid takes a few ms.
"""

from __future__ import annotations

import base64
import zlib
from typing import Any

WALL = -1
FLOOR = -2
MAX_PATH_POINTS = 4000


def _decode_grid(payload: dict[str, Any]) -> tuple[bytes, int, int, float, float, float] | None:
    width, height = payload.get("width"), payload.get("height")
    data = payload.get("map_data")
    if not data or not width or not height:
        return None
    raw = zlib.decompress(base64.b64decode(data))
    width, height = int(width), int(height)
    if len(raw) < width * height:
        return None
    res = float(payload.get("resolution", 50) or 50)
    return raw, width, height, res, float(payload.get("origin_x", 0)), float(payload.get("origin_y", 0))


def _grid_to_room(payload: dict[str, Any]) -> dict[int, int]:
    mapping: dict[int, int] = {}
    for entry in payload.get("map_room_info") or []:
        if isinstance(entry, dict) and entry.get("grid_id") is not None and entry.get("room_id") is not None:
            try:
                mapping[int(entry["grid_id"])] = int(entry["room_id"])
            except (TypeError, ValueError):
                continue
    return mapping


def _trace(cells: list[int], width: int, height: int, cls: int) -> list[list[tuple[int, int]]]:
    """Outlines of class `cls` as closed loops of grid vertices (counter-clockwise, y up)."""
    out: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def add(a: tuple[int, int], b: tuple[int, int]) -> None:
        out.setdefault(a, []).append(b)

    for y in range(height):
        row = y * width
        for x in range(width):
            if cells[row + x] != cls:
                continue
            if y == 0 or cells[row - width + x] != cls:
                add((x, y), (x + 1, y))
            if x == width - 1 or cells[row + x + 1] != cls:
                add((x + 1, y), (x + 1, y + 1))
            if y == height - 1 or cells[row + width + x] != cls:
                add((x + 1, y + 1), (x, y + 1))
            if x == 0 or cells[row + x - 1] != cls:
                add((x, y + 1), (x, y))

    loops: list[list[tuple[int, int]]] = []
    while out:
        start = next(iter(out))
        loop = [start]
        prev = start
        cur = out[start].pop()
        if not out[start]:
            del out[start]
        while cur != start:
            nexts = out.get(cur)
            if not nexts:
                break
            if len(nexts) > 1:
                # Corner touch: prefer the left turn so loops never cross.
                dx, dy = cur[0] - prev[0], cur[1] - prev[1]
                left = (cur[0] - dy, cur[1] + dx)
                straight = (cur[0] + dx, cur[1] + dy)
                pick = next((n for n in (left, straight) if n in nexts), nexts[0])
                nexts.remove(pick)
                nxt = pick
            else:
                nxt = nexts.pop()
            if not nexts:
                del out[cur]
            loop.append(cur)
            prev, cur = cur, nxt
        # drop collinear points
        simple = [
            p
            for i, p in enumerate(loop)
            if (loop[i - 1][0] - p[0]) * (loop[(i + 1) % len(loop)][1] - p[1])
            != (loop[i - 1][1] - p[1]) * (loop[(i + 1) % len(loop)][0] - p[0])
        ]
        if len(simple) >= 3:
            loops.append(simple)
    return loops


def _room_at(cells: list[int], width: int, height: int, cx: int, cy: int, radius: int = 5) -> int | None:
    """Room under the robot by pixel; in doorways / unlabelled floor the nearest room within `radius` cells."""
    best: tuple[int, int] | None = None
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            x, y = cx + dx, cy + dy
            if 0 <= x < width and 0 <= y < height and cells[y * width + x] > 0:
                d = dx * dx + dy * dy
                if best is None or d < best[0]:
                    best = (d, cells[y * width + x])
    return best[1] if best else None


def build_geometry(
    payload: dict[str, Any], names: dict[int, str] | None = None, position: tuple[float, float] | None = None
) -> dict[str, Any] | None:
    decoded = _decode_grid(payload)
    if decoded is None:
        return None
    raw, width, height, res, ox, oy = decoded
    g2r = _grid_to_room(payload)

    cells = [0] * (width * height)
    sums: dict[int, list[float]] = {}
    for i in range(width * height):
        v = raw[i]
        if v == 0:
            continue
        if v in (1, 2):
            cells[i] = FLOOR
        elif 3 <= v <= 63:
            rid = g2r.get(v, v)
            cells[i] = rid
            s = sums.setdefault(rid, [0.0, 0.0, 0.0])
            s[0] += i % width
            s[1] += i // width
            s[2] += 1
        else:
            cells[i] = WALL

    def world(p: tuple[float, float]) -> list[float]:
        return [round(ox + p[0] * res), round(oy + p[1] * res)]

    def polys(cls: int) -> list[list[list[float]]]:
        return [[world(p) for p in loop] for loop in _trace(cells, width, height, cls)]

    rooms = []
    for rid, (sx, sy, n) in sorted(sums.items()):
        cx, cy = sx / n, sy / n
        ix, iy = int(cx), int(cy)
        if not (0 <= ix < width and 0 <= iy < height) or cells[iy * width + ix] != rid:
            # centroid outside the room (L shape) -> nearest cell of the room
            best = None
            for i, c in enumerate(cells):
                if c == rid:
                    d = (i % width - cx) ** 2 + (i // width - cy) ** 2
                    if best is None or d < best[0]:
                        best = (d, i % width, i // width)
            if best:
                cx, cy = best[1], best[2]
        rooms.append(
            {
                "id": rid,
                "name": (names or {}).get(rid, ""),
                "area_m2": round(n * res * res / 1e6, 1),
                "label": world((cx + 0.5, cy + 0.5)),
                "polygons": polys(rid),
            }
        )

    path: list[list[float]] = []
    paths = payload.get("paths")
    src = paths.get("points") if isinstance(paths, dict) else paths
    if isinstance(src, list):
        pts = [p for p in src if isinstance(p, dict) and "x" in p and "y" in p]
        step = max(1, len(pts) // MAX_PATH_POINTS)
        path = [[round(p["x"]), round(p["y"])] for p in pts[::step]]

    vacuum_room = None
    if position is not None:
        vacuum_room = _room_at(cells, width, height, int((position[0] - ox) // res), int((position[1] - oy) // res))

    return {
        "version": 1,
        "vacuum_room": vacuum_room,
        "resolution": res,
        "bounds": [round(ox), round(oy), round(ox + width * res), round(oy + height * res)],
        "rooms": rooms,
        "walls": polys(WALL),
        "floor": polys(FLOOR),
        "path": path,
        # Raw yaw from the payload: milliradians, counter-clockwise (1570 = 90 deg).
        "yaw": {
            "vacuum": (payload.get("position") or {}).get("yaw") if isinstance(payload.get("position"), dict) else None,
            "charger": payload.get("pile_yaw"),
        },
    }
