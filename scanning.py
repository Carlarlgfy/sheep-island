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

        # ── sheep ↔ sheep (symmetric, one dist_sq per pair) ──────────────
        n = len(sheep_list)
        for i in range(n):
            a  = sheep_list[i]
            ax = a.tx
            ay = a.ty
            for j in range(i + 1, n):
                b  = sheep_list[j]
                dx = b.tx - ax
                dy = b.ty - ay
                d2 = dx * dx + dy * dy
                if d2 < _SHEEP_SQ:
                    a.nearby_sheep.append((b, d2))
                    b.nearby_sheep.append((a, d2))

        # ── wolf ↔ sheep (asymmetric thresholds, one dist_sq per pair) ───
        for w in wolf_list:
            wx = w.tx
            wy = w.ty
            for s in sheep_list:
                dx = s.tx - wx
                dy = s.ty - wy
                d2 = dx * dx + dy * dy
                if d2 < _WS_SQ:
                    w.nearby_sheep.append((s, d2))
                if d2 < _SW_SQ:
                    s.nearby_wolves.append((w, d2))

        # ── wolf ↔ wolf (symmetric) ───────────────────────────────────────
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
