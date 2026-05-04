"""Unified proximity scanner.

Call ProximityScanner.update(sheep_list, wolf_list) once per frame, before
any entity updates.  It writes two pre-filtered neighbor lists onto every
entity so behaviors iterate a small local list instead of the full population.

After update():
  entity.nearby_sheep  — list of (Sheep, dist_sq) within the scan threshold
  entity.nearby_wolves — list of (Wolf,  dist_sq) within the scan threshold

dist_sq values are from the *previous* frame (positions at scan time).
Behaviors that need a fresh direction should recompute dx/dy from current
positions; the list is used purely for cheap pre-filtering.
"""

# Scan radii — each covers the largest behavior of that pair type.
# Behaviors apply their own tighter threshold when they consume the list.
SHEEP_SCAN_R      = 80.0    # sheep↔sheep: WANDERER_ATTRACT_RADIUS (widest)
WOLF_SHEEP_SCAN_R = 700.0   # wolf sees sheep/corpses: WOLF_SMELL_RADIUS (widest)
SHEEP_WOLF_SCAN_R = 180.0   # sheep sees wolves: SHEEP_WOLF_AWARENESS_R in herd.py
WOLF_WOLF_SCAN_R  = 36.0    # wolf↔wolf: mate-seek outer limit (widest)

_SHEEP_SQ = SHEEP_SCAN_R      * SHEEP_SCAN_R
_WS_SQ    = WOLF_SHEEP_SCAN_R * WOLF_SHEEP_SCAN_R
_SW_SQ    = SHEEP_WOLF_SCAN_R * SHEEP_WOLF_SCAN_R
_WW_SQ    = WOLF_WOLF_SCAN_R  * WOLF_WOLF_SCAN_R

MAX_SHEEP_NEIGHBORS = 48
MAX_WOLF_SHEEP_NEIGHBORS = 96


def _nearby_cells(cx: int, cy: int, radius_cells: int):
    for oy in range(-radius_cells, radius_cells + 1):
        for ox in range(-radius_cells, radius_cells + 1):
            yield cx + ox, cy + oy


_SHEEP_CELL_OFFSETS = sorted(
    [(ox, oy) for oy in range(-5, 6) for ox in range(-5, 6)],
    key=lambda p: p[0] * p[0] + p[1] * p[1],
)


def _trim_neighbors(entity, attr: str, limit: int):
    neighbors = getattr(entity, attr, [])
    if len(neighbors) > limit:
        neighbors.sort(key=lambda item: item[1])
        del neighbors[limit:]


class ProximityScanner:
    """Compute pairwise distances once per frame; write filtered neighbor lists."""

    def update(self, sheep_list: list, wolf_list: list) -> None:
        # Reset neighbor lists on all entities
        for s in sheep_list:
            s.nearby_sheep  = []
            s.nearby_wolves = []
        for w in wolf_list:
            w.nearby_sheep  = []
            w.nearby_wolves = []

        # ── sheep → sheep (spatial hash, capped nearest-neighbor scan) ───
        sheep_cell = 16.0
        sheep_grid: dict[tuple[int, int], list] = {}
        for s in sheep_list:
            key = (int(s.tx // sheep_cell), int(s.ty // sheep_cell))
            sheep_grid.setdefault(key, []).append(s)

        for s in sheep_list:
            sx = s.tx
            sy = s.ty
            scx = int(sx // sheep_cell)
            scy = int(sy // sheep_cell)
            neighbors = s.nearby_sheep
            for ox, oy in _SHEEP_CELL_OFFSETS:
                bucket = sheep_grid.get((scx + ox, scy + oy))
                if not bucket:
                    continue
                for other in bucket:
                    if other is s:
                        continue
                    dx = other.tx - sx
                    dy = other.ty - sy
                    d2 = dx * dx + dy * dy
                    if d2 < _SHEEP_SQ:
                        neighbors.append((other, d2))
                        if len(neighbors) >= MAX_SHEEP_NEIGHBORS:
                            break
                if len(neighbors) >= MAX_SHEEP_NEIGHBORS:
                    break

        # ── wolf ↔ sheep (asymmetric thresholds, spatial hash) ───────────
        wolf_sheep_cell = WOLF_SHEEP_SCAN_R
        wide_sheep_grid: dict[tuple[int, int], list] = {}
        for s in sheep_list:
            key = (int(s.tx // wolf_sheep_cell), int(s.ty // wolf_sheep_cell))
            wide_sheep_grid.setdefault(key, []).append(s)

        for w in wolf_list:
            wx = w.tx
            wy = w.ty
            wcx = int(wx // wolf_sheep_cell)
            wcy = int(wy // wolf_sheep_cell)
            for key in _nearby_cells(wcx, wcy, 1):
                for s in wide_sheep_grid.get(key, []):
                    dx = s.tx - wx
                    dy = s.ty - wy
                    d2 = dx * dx + dy * dy
                    if d2 < _WS_SQ:
                        w.nearby_sheep.append((s, d2))
                    if d2 < _SW_SQ:
                        s.nearby_wolves.append((w, d2))
            _trim_neighbors(w, "nearby_sheep", MAX_WOLF_SHEEP_NEIGHBORS)

        # Sheep awareness of wolves uses the smaller radius, so index wolves
        # separately to avoid checking every sheep against every wolf.
        sheep_wolf_cell = SHEEP_WOLF_SCAN_R
        wolf_grid: dict[tuple[int, int], list] = {}
        for w in wolf_list:
            key = (int(w.tx // sheep_wolf_cell), int(w.ty // sheep_wolf_cell))
            wolf_grid.setdefault(key, []).append(w)

        for s in sheep_list:
            sx = s.tx
            sy = s.ty
            scx = int(sx // sheep_wolf_cell)
            scy = int(sy // sheep_wolf_cell)
            for key in _nearby_cells(scx, scy, 1):
                for w in wolf_grid.get(key, []):
                    dx = w.tx - sx
                    dy = w.ty - sy
                    d2 = dx * dx + dy * dy
                    if d2 < _SW_SQ:
                        # Avoid duplicates with the wolf-smell pass.
                        if not any(existing is w for existing, _ in s.nearby_wolves):
                            s.nearby_wolves.append((w, d2))

        # ── wolf ↔ wolf (symmetric, small population, direct loop) ───────
        nw = len(wolf_list)
        for i in range(nw):
            a  = wolf_list[i]
            ax = a.tx
            ay = a.ty
            for j in range(i + 1, nw):
                b  = wolf_list[j]
                dx = b.tx - ax
                dy = b.ty - ay
                d2 = dx * dx + dy * dy
                if d2 < _WW_SQ:
                    a.nearby_wolves.append((b, d2))
                    b.nearby_wolves.append((a, d2))
