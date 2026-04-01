"""
HerdManager — proximity-based herding with center-of-gravity, parent bonds,
and collective migration.

Each animal needs:
    .tx, .ty            : float  — tile position
    .herd_id            : int    — assigned here; -1 = unassigned
    .curiosity          : float  — 0-1; higher = more likely to defect/wander
    .hunger             : float  — 0-1; used for emergency migration trigger
    .age                : float  — sim-seconds; older sheep feel stronger pull to center

HerdManager writes these attributes onto each animal every frame:
    .herd_cx, .herd_cy      — herd center of mass (tile coords)
    .herd_pull_strength     — cohesion weight toward center (age-scaled; amplified when gathering)
    .migration_mode         — bool: herd is moving as one
    .migrate_dx/dy          — unit vector: direction of current migration
"""

import math
import random

from mapgen import WATER, GRASS, DIRT

# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------

HERD_PROXIMITY      = 22.0   # tiles — max distance to be in the same herd
REASSIGN_INTERVAL   = 10.0   # sim-seconds between flood-fill reassignments
CURIOSITY_SWITCH    = 0.08   # base defection probability per reassignment cycle

# Herd merging
MERGE_TRIGGER_RADIUS = 10.0  # tiles — members this close make two herds merge-eligible
MERGE_CHANCE         = 0.04  # probability of merge per eligible pair per reassignment
GRASS_SNAP_RADIUS    = 35    # max tile radius searched when snapping CoG to grass

# Gravitational pull toward herd center
GRAVITY_MATURE      = 0.60   # pull weight for fully mature sheep
GRAVITY_YOUNG       = 0.18   # pull weight for lambs / young adults
MATURITY_GRAVITY_AGE = 270.0 # sim-secs at which full gravity kicks in

# Parent bond
PARENT_PULL         = 0.70   # extra pull weight toward parent for young sheep
PARENT_AGE_CUTOFF   = 180.0  # sim-secs — bond fades linearly to zero by this age

# Gathering / migration state machine
MIGRATION_INTERVAL_MIN  = 120.0   # sim-secs between normal migrations
MIGRATION_INTERVAL_MAX  = 360.0
GATHER_DURATION         = 18.0    # gathering phase before the herd moves
MIGRATION_DURATION_MIN  = 50.0
MIGRATION_DURATION_MAX  = 110.0
GATHER_PULL_BOOST       = 0.95    # herd_pull_strength during gathering (overrides age calc)
HUNGER_MIGRATE_THRESHOLD = 0.58   # avg herd hunger that triggers emergency migration
HUNGER_MIGRATE_END       = 0.38   # avg hunger below this ends emergency migration


# ---------------------------------------------------------------------------
# Per-herd state container
# ---------------------------------------------------------------------------

class _HerdData:
    __slots__ = ("cx", "cy", "state", "timer", "mdx", "mdy",
                 "avg_hunger", "size", "emergency")

    IDLE      = "idle"
    GATHERING = "gathering"
    MIGRATING = "migrating"

    def __init__(self):
        self.cx         = 0.0
        self.cy         = 0.0
        self.state      = _HerdData.IDLE
        self.timer      = random.uniform(MIGRATION_INTERVAL_MIN, MIGRATION_INTERVAL_MAX)
        self.mdx        = 0.0
        self.mdy        = 0.0
        self.avg_hunger = 0.0
        self.size       = 0
        self.emergency  = False


# ---------------------------------------------------------------------------
# HerdManager
# ---------------------------------------------------------------------------

class HerdManager:
    """
    Two-phase update:
      1. Every REASSIGN_INTERVAL: flood-fill proximity grouping + curiosity defection.
      2. Every frame: compute center-of-mass, tick state machines, push
         herd_cx/cy/migration_mode/migrate_dx/dy/herd_pull_strength onto each animal.
    """

    def __init__(self):
        self._timer  = 0.0
        self._herds: dict[int, _HerdData] = {}
        self._grid   = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def update(self, dt: float, animals: list, grid: list = None):
        if grid is not None:
            self._grid = grid
        self._timer -= dt
        if self._timer <= 0:
            self._timer = REASSIGN_INTERVAL
            if animals:
                self._reassign(animals)

        if animals:
            self._update_herds(dt, animals)

    # ------------------------------------------------------------------
    # Reassignment (flood-fill + curiosity defection)
    # ------------------------------------------------------------------

    def _reassign(self, animals: list):
        cell = HERD_PROXIMITY

        # Spatial hash
        spatial: dict[tuple, list] = {}
        for a in animals:
            key = (int(a.tx / cell), int(a.ty / cell))
            spatial.setdefault(key, []).append(a)

        # Track old ids for state continuity
        old_ids = {id(a): a.herd_id for a in animals}

        for a in animals:
            a.herd_id = -1

        herd_id = 0
        for seed in animals:
            if seed.herd_id != -1:
                continue
            seed.herd_id = herd_id
            stack = [seed]
            while stack:
                a = stack.pop()
                cx, cy = int(a.tx / cell), int(a.ty / cell)
                for dcx in (-1, 0, 1):
                    for dcy in (-1, 0, 1):
                        for candidate in spatial.get((cx + dcx, cy + dcy), []):
                            if candidate.herd_id != -1:
                                continue
                            ddx = candidate.tx - a.tx
                            ddy = candidate.ty - a.ty
                            if ddx * ddx + ddy * ddy <= cell * cell:
                                candidate.herd_id = herd_id
                                stack.append(candidate)
            herd_id += 1

        self._apply_curiosity_switches(animals, spatial, cell)
        self._maybe_merge_herds(animals)

        # Build new herd dict, carrying over state from old herds where possible
        # (so migrations don't reset every reassignment)
        new_herds: dict[int, _HerdData] = {}
        for a in animals:
            if a.herd_id not in new_herds:
                new_herds[a.herd_id] = _HerdData()

        # For each old herd find which new herd inherited most of its members
        old_to_new_votes: dict[int, list] = {}
        for a in animals:
            old_id = old_ids.get(id(a), -1)
            if old_id >= 0:
                old_to_new_votes.setdefault(old_id, []).append(a.herd_id)

        transferred: set[int] = set()
        for old_id, votes in old_to_new_votes.items():
            if old_id not in self._herds:
                continue
            best_new = max(set(votes), key=votes.count)
            if best_new in new_herds and best_new not in transferred:
                old_data = self._herds[old_id]
                nd = new_herds[best_new]
                nd.state     = old_data.state
                nd.timer     = old_data.timer
                nd.mdx       = old_data.mdx
                nd.mdy       = old_data.mdy
                nd.emergency = old_data.emergency
                transferred.add(best_new)

        self._herds = new_herds

    def _apply_curiosity_switches(self, animals: list,
                                  spatial: dict, cell: float):
        """High-curiosity animals occasionally migrate to an adjacent herd."""
        for a in animals:
            if a.curiosity < 0.5:
                continue
            if random.random() > a.curiosity * CURIOSITY_SWITCH:
                continue
            cx, cy = int(a.tx / cell), int(a.ty / cell)
            for dcx in (-1, 0, 1):
                for dcy in (-1, 0, 1):
                    for candidate in spatial.get((cx + dcx, cy + dcy), []):
                        if candidate.herd_id == a.herd_id:
                            continue
                        ddx = candidate.tx - a.tx
                        ddy = candidate.ty - a.ty
                        if ddx * ddx + ddy * ddy <= cell * cell:
                            a.herd_id = candidate.herd_id
                            break
                    else:
                        continue
                    break

    # ------------------------------------------------------------------
    # Occasional herd merging
    # ------------------------------------------------------------------

    def _maybe_merge_herds(self, animals: list):
        """Occasionally merge two herds whose members are very close together."""
        by_herd: dict[int, list] = {}
        for a in animals:
            if a.herd_id >= 0:
                by_herd.setdefault(a.herd_id, []).append(a)

        herd_ids = list(by_herd.keys())
        merged: set[int] = set()
        thr_sq = MERGE_TRIGGER_RADIUS ** 2

        for i in range(len(herd_ids)):
            h1 = herd_ids[i]
            if h1 in merged:
                continue
            for j in range(i + 1, len(herd_ids)):
                h2 = herd_ids[j]
                if h2 in merged or random.random() > MERGE_CHANCE:
                    continue
                m1 = by_herd[h1]
                m2 = by_herd[h2]
                close = any(
                    (a1.tx - a2.tx) ** 2 + (a1.ty - a2.ty) ** 2 < thr_sq
                    for a1 in m1[:5] for a2 in m2[:5]
                )
                if not close:
                    continue
                # Merge smaller herd into larger
                if len(m1) >= len(m2):
                    for a in m2:
                        a.herd_id = h1
                    merged.add(h2)
                    by_herd[h1].extend(m2)
                else:
                    for a in m1:
                        a.herd_id = h2
                    merged.add(h1)
                    by_herd[h2].extend(m1)
                    break  # h1 consumed; advance outer loop

    # ------------------------------------------------------------------
    # Terrain helpers
    # ------------------------------------------------------------------

    def _nearest_grass_pt(self, cx: float, cy: float) -> tuple[float, float]:
        """Return the nearest grass tile centre to (cx, cy).
        Returns (cx, cy) unchanged if already on grass or no grass found."""
        if self._grid is None:
            return cx, cy
        rows = len(self._grid)
        cols = len(self._grid[0]) if rows else 0
        ic, ir = int(cx), int(cy)
        if 0 <= ir < rows and 0 <= ic < cols and self._grid[ir][ic] == GRASS:
            return cx, cy
        best_dist_sq = float('inf')
        best_c, best_r = ic, ir
        found = False
        for radius in range(1, GRASS_SNAP_RADIUS + 1):
            # Early exit: minimum dist at this ring is `radius`; if already found closer, stop
            if found and radius * radius > best_dist_sq:
                break
            for dr in range(-radius, radius + 1):
                for dc in range(-radius, radius + 1):
                    if abs(dr) != radius and abs(dc) != radius:
                        continue   # only the border ring
                    r, c = ir + dr, ic + dc
                    if 0 <= r < rows and 0 <= c < cols and self._grid[r][c] == GRASS:
                        d = dr * dr + dc * dc
                        if d < best_dist_sq:
                            best_dist_sq = d
                            best_c, best_r = c, r
                            found = True
        if found:
            return float(best_c) + 0.5, float(best_r) + 0.5
        return cx, cy

    # ------------------------------------------------------------------
    # Per-frame herd state machine + attribute injection
    # ------------------------------------------------------------------

    def _update_herds(self, dt: float, animals: list):
        # Group by herd id
        by_herd: dict[int, list] = {}
        for a in animals:
            if a.herd_id >= 0:
                by_herd.setdefault(a.herd_id, []).append(a)

        for hid, members in by_herd.items():
            data = self._herds.get(hid)
            if data is None:
                data = _HerdData()
                self._herds[hid] = data

            n = len(members)
            cx = sum(a.tx for a in members) / n
            cy = sum(a.ty for a in members) / n
            avg_h = sum(a.hunger for a in members) / n

            data.cx         = cx
            data.cy         = cy
            data.avg_hunger = avg_h
            data.size       = n

            # Snap the cohesion target to the nearest grass tile so the
            # gravitational pull never drags the herd toward water/beach.
            eff_cx, eff_cy = self._nearest_grass_pt(cx, cy)

            # --- State machine ---
            data.timer -= dt

            if data.state == _HerdData.IDLE:
                if avg_h >= HUNGER_MIGRATE_THRESHOLD and n >= 2:
                    # Emergency migration — gather fast
                    data.state     = _HerdData.GATHERING
                    data.timer     = GATHER_DURATION * 0.4
                    data.emergency = True
                elif data.timer <= 0:
                    data.state     = _HerdData.GATHERING
                    data.timer     = GATHER_DURATION
                    data.emergency = False

            elif data.state == _HerdData.GATHERING:
                if data.timer <= 0:
                    data.mdx, data.mdy = self._pick_migration_dir(
                        data.cx, data.cy, self._grid)
                    data.state = _HerdData.MIGRATING
                    data.timer = random.uniform(MIGRATION_DURATION_MIN,
                                                MIGRATION_DURATION_MAX)

            elif data.state == _HerdData.MIGRATING:
                end_condition = (data.timer <= 0 or
                                 (data.emergency and avg_h < HUNGER_MIGRATE_END))
                if end_condition:
                    data.state     = _HerdData.IDLE
                    data.timer     = random.uniform(MIGRATION_INTERVAL_MIN,
                                                    MIGRATION_INTERVAL_MAX)
                    data.emergency = False

            # --- Push attributes onto each member ---
            gathering  = (data.state == _HerdData.GATHERING)
            migrating  = (data.state == _HerdData.MIGRATING)

            for a in members:
                a.herd_cx  = eff_cx
                a.herd_cy  = eff_cy
                a.migration_mode = migrating
                a.migrate_dx     = data.mdx
                a.migrate_dy     = data.mdy

                if gathering:
                    # Strong pull toward center during gathering
                    a.herd_pull_strength = GATHER_PULL_BOOST
                else:
                    # Gravity scales with age: young sheep pull less, old more
                    age_frac = min(1.0, a.age / MATURITY_GRAVITY_AGE)
                    a.herd_pull_strength = (GRAVITY_YOUNG
                                            + (GRAVITY_MATURE - GRAVITY_YOUNG) * age_frac)

    # ------------------------------------------------------------------
    # Terrain-aware migration direction
    # ------------------------------------------------------------------

    @staticmethod
    def _pick_migration_dir(cx: float, cy: float,
                            grid: list) -> tuple[float, float]:
        """
        Sample 16 candidate directions; score each by how much grass/dirt
        lies ahead and how little water.  Returns (dx, dy) unit vector.
        """
        if grid is None:
            angle = random.uniform(0, 2 * math.pi)
            return math.cos(angle), math.sin(angle)

        rows = len(grid)
        cols = len(grid[0]) if rows else 0

        n_candidates = 16
        best_angle = random.uniform(0, 2 * math.pi)
        best_score = -9999

        for i in range(n_candidates):
            angle = i * (2 * math.pi / n_candidates)
            adx   = math.cos(angle)
            ady   = math.sin(angle)
            score = 0

            # Walk up to 35 tiles ahead, sampling every 3 tiles
            for step in range(4, 36, 3):
                nx = int(cx + adx * step)
                ny = int(cy + ady * step)
                if 0 <= ny < rows and 0 <= nx < cols:
                    t = grid[ny][nx]
                    if t == GRASS:
                        score += 4
                    elif t == DIRT:
                        score += 2
                    elif t == WATER:
                        score -= 6   # strongly penalise water ahead
                    # sand: score unchanged
                else:
                    score -= 4       # penalise heading off-map

            if score > best_score:
                best_score = score
                best_angle = angle

        # Small noise so herds don't all march in axis-aligned lines
        best_angle += random.gauss(0, 0.15)
        return math.cos(best_angle), math.sin(best_angle)
