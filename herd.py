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
    .herd_awareness_r       — awareness radius of the herd (tile coords)
    .herd_pull_strength     — cohesion weight toward center (age-scaled; amplified when gathering)
    .migration_mode         — bool: herd is moving as one
    .migrate_dx/dy          — unit vector: direction of current migration
"""

import math
import random

from mapgen import WATER, SAND, GRASS, DIRT

# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------

HERD_PROXIMITY      = 12.0   # tiles — max distance to be in the same herd (covers 10-20 tile spawn area)
REASSIGN_INTERVAL   = 10.0   # sim-seconds between flood-fill reassignments
CURIOSITY_SWITCH    = 0.03   # base defection probability per reassignment cycle

# Herd merging
MERGE_TRIGGER_RADIUS = 5.0   # tiles — members this close make two herds merge-eligible
MERGE_CHANCE         = 0.14  # probability of merge per eligible pair per reassignment
GRASS_SNAP_RADIUS    = 35    # max tile radius searched when snapping CoG to grass

# Gravitational pull toward herd center — WAY stronger than before
GRAVITY_MATURE       = 28.0   # was 9.0
GRAVITY_YOUNG        = 14.0   # was 4.5
MATURITY_GRAVITY_AGE = 900.0  # sim-secs at which full gravity kicks in

# Hard boundary push: non-wanderers (curiosity < 0.3) beyond awareness radius
# get this additional force per unit of excess distance fraction
HERD_BOUNDARY_PUSH   = 90.0  # overwhelming force to snap strays back

# Parent bond
PARENT_PULL          = 0.70
PARENT_AGE_CUTOFF    = 600.0  # 2 days — matches sheep.py

# Gathering / migration state machine
MIGRATION_INTERVAL_MIN  = 600.0   # sim-secs between fallback time-based migrations (rare)
MIGRATION_INTERVAL_MAX  = 1200.0
GATHER_DURATION         = 18.0    # gathering phase before the herd moves
MIGRATION_DURATION_MIN  = 50.0
MIGRATION_DURATION_MAX  = 110.0
GATHER_PULL_BOOST       = 20.0    # was 14.0 — stronger gathering cohesion
HUNGER_MIGRATE_THRESHOLD = 0.55
HUNGER_MIGRATE_END       = 0.38

# Grass scarcity migration trigger — primary migration driver
GRASS_MIGRATE_THRESHOLD = 0.25   # migrate when <25% of land in awareness radius is grass

# Awareness radius cap — prevents the herd from tolerating already-dispersed sheep
AWARENESS_R_CAP     = 20.0  # max awareness radius regardless of actual spread

# Shared grazing patch — all herd members prefer eating here rather than scattering
GRAZE_PATCH_RADIUS  = 14    # tile radius from herd center searched for best local grass
GRAZE_REFRESH_TIME  = 6.0   # seconds between grazing patch recalculations

# ---------------------------------------------------------------------------
# Dirt-terrain migration  (fallback: move away when local area is mostly bare dirt)
# ---------------------------------------------------------------------------
DIRT_SCAN_RADIUS       = 10     # tile radius inspected around herd center
DIRT_MIGRATE_THRESHOLD = 0.65   # migrate when >65% of non-water/sand land is dirt

# ---------------------------------------------------------------------------
# Wolf awareness and flee
# ---------------------------------------------------------------------------
SHEEP_WOLF_AWARENESS_R    = 50.0   # tiles — individual sheep spot any wolf within this range
SHEEP_WOLF_SPOOK_FRAC     = 0.20   # fraction of herd that must be wolf_aware to trigger flee
SHEEP_FLEE_MIN_DIST       = 80.0   # tiles — minimum flee-migration distance from wolf threat
SHEEP_CORPSE_AVOID_DIST   = 50.0   # tiles — flee target penalty radius around corpses / wolves

# ---------------------------------------------------------------------------
# Death-panic migration  (exponential urgency as herd members die)
# ---------------------------------------------------------------------------
DEATH_SCAN_RADIUS      = 35.0   # tile radius to count nearby corpses for panic
DEATH_PANIC_SCALE      = 2.5    # exponential rate: panic = exp(deaths/herd * scale) - 1
DEATH_PANIC_THRESHOLD  = 0.20   # panic level that begins cutting the idle timer
DEATH_FLEE_DIST_MULT   = 3.0    # max extra distance multiplier at full panic (additive)


# ---------------------------------------------------------------------------
# Per-herd state container
# ---------------------------------------------------------------------------

class _HerdData:
    __slots__ = ("cx", "cy", "state", "timer", "mtx", "mty",
                 "avg_hunger", "size", "emergency",
                 "nearby_deaths", "panic_level", "flee_dist_mult",
                 "awareness_r",
                 "graze_cx", "graze_cy", "graze_refresh")

    IDLE      = "idle"
    GATHERING = "gathering"
    MIGRATING = "migrating"

    def __init__(self):
        self.cx              = 0.0
        self.cy              = 0.0
        self.state           = _HerdData.IDLE
        self.timer           = random.uniform(MIGRATION_INTERVAL_MIN, MIGRATION_INTERVAL_MAX)
        self.mtx             = 0.0
        self.mty             = 0.0
        self.avg_hunger      = 0.0
        self.size            = 0
        self.emergency       = False
        self.nearby_deaths   = 0      # corpses within DEATH_SCAN_RADIUS of herd center
        self.panic_level     = 0.0    # exp(death_ratio * scale) - 1
        self.flee_dist_mult  = 1.0    # distance multiplier applied to migration target
        self.awareness_r     = 20.0   # herd awareness radius (updated every frame)
        self.graze_cx        = 0.0    # shared grazing patch center x (tile coords)
        self.graze_cy        = 0.0    # shared grazing patch center y (tile coords)
        self.graze_refresh   = 0.0    # countdown to next grazing patch recalculation


# ---------------------------------------------------------------------------
# HerdManager
# ---------------------------------------------------------------------------

class HerdManager:
    """
    Two-phase update:
      1. Every REASSIGN_INTERVAL: flood-fill proximity grouping + curiosity defection.
      2. Every frame: compute center-of-mass, tick state machines, push
         herd_cx/cy/herd_awareness_r/migration_mode/migrate_dx/dy/herd_pull_strength
         onto each animal.
    """

    def __init__(self):
        self._timer  = 0.0
        self._herds: dict[int, _HerdData] = {}
        self._grid   = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def update(self, dt: float, animals: list, grid: list = None,
               wolves: list = None):
        if grid is not None:
            self._grid = grid
        living  = [a for a in animals if getattr(a, 'dead_state', None) is None]
        corpses = [a for a in animals if getattr(a, 'dead_state', None) is not None]
        self._timer -= dt
        if self._timer <= 0:
            self._timer = REASSIGN_INTERVAL
            if living:
                self._reassign(living)

        if living:
            self._update_herds(dt, living, corpses)

        # Wolf-threat: trigger emergency flee migrations for herds near hunting wolves
        if wolves and living:
            self._apply_wolf_threat(living, wolves)

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
                nd.state      = old_data.state
                nd.timer      = old_data.timer
                nd.mtx        = old_data.mtx
                nd.mty        = old_data.mty
                nd.emergency  = old_data.emergency
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

    def _herd_awareness_radius(self, cx: float, cy: float,
                               members: list) -> float:
        """
        Aggregate awareness radius: max distance from center to any member,
        extended 20% beyond the outermost sheep so the herd has a loose vision
        cone slightly larger than its physical spread.
        """
        if not members:
            return HERD_PROXIMITY * 0.5
        max_dist = 0.0
        for a in members:
            d = math.sqrt((a.tx - cx) ** 2 + (a.ty - cy) ** 2)
            if d > max_dist:
                max_dist = d
        # Cap at AWARENESS_R_CAP so a dispersed herd doesn't grow tolerant of its own spread
        return max(10.0, min(AWARENESS_R_CAP, max_dist * 1.2))

    def _pick_migration_target(self, cx: float, cy: float,
                               awareness_r: float,
                               flee_mult: float = 1.0,
                               avoid_positions: list = None,
                               min_dist: float = 0.0) -> tuple[float, float]:
        """
        Sample 3–4 candidate patches spread evenly across 360°, each at
        distance awareness_r×2×flee_mult from the herd center.  Score each by
        the number of grass tiles in a 5×5 window around the candidate.
        flee_mult > 1 means the herd is fleeing and should migrate farther.
        avoid_positions: list of (x, y) points to steer away from (wolves, corpses).
        min_dist: minimum candidate distance regardless of awareness_r.
        """
        grid = self._grid
        target_dist = max(min_dist, max(20.0, awareness_r * 2.0) * flee_mult)

        if grid is None:
            angle = random.uniform(0, 2 * math.pi)
            return cx + math.cos(angle) * target_dist, cy + math.sin(angle) * target_dist

        rows = len(grid)
        cols = len(grid[0]) if rows else 0

        n_candidates = random.randint(3, 4)
        base_angle   = random.uniform(0, 2 * math.pi)
        candidates   = []   # (px, py, grass_count, avoid_penalty)

        for i in range(n_candidates):
            angle = base_angle + i * (2 * math.pi / n_candidates)
            angle += random.gauss(0, 0.15)

            px = cx + math.cos(angle) * target_dist
            py = cy + math.sin(angle) * target_dist
            px = max(2.0, min(cols - 3.0, px))
            py = max(2.0, min(rows - 3.0, py))

            # Count grass in 5×5 patch
            ipx, ipy = int(px), int(py)
            grass_count = 0
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    r, c = ipy + dr, ipx + dc
                    if 0 <= r < rows and 0 <= c < cols and grid[r][c] == GRASS:
                        grass_count += 1

            # Penalty for being near wolves or corpses
            avoid_penalty = 0.0
            if avoid_positions:
                for ax, ay in avoid_positions:
                    d = math.sqrt((px - ax) ** 2 + (py - ay) ** 2)
                    if d < SHEEP_CORPSE_AVOID_DIST:
                        avoid_penalty += (SHEEP_CORPSE_AVOID_DIST - d) / SHEEP_CORPSE_AVOID_DIST * 3.0

            candidates.append((px, py, grass_count, avoid_penalty))

        # Build weights — grass density bonus, avoid-penalty malus
        max_grass = max(g for _, _, g, _ in candidates)
        weights   = []
        for _, _, gc, ap in candidates:
            if max_grass > 0 and gc == 0:
                w = 0.02          # nearly impossible to choose a barren patch
            elif max_grass == 0:
                w = 1.0           # all equally unknown — pure random
            else:
                w = 0.1 + (gc / max_grass) * 2.0   # 0.1..2.1 proportional to density
            w = max(0.01, w - ap * 0.5)             # penalise proximity to threats
            weights.append(w)

        # Weighted random pick
        total_w    = sum(weights)
        pick       = random.uniform(0, total_w)
        cumulative = 0.0
        chosen     = candidates[0]
        for i, w in enumerate(weights):
            cumulative += w
            if pick <= cumulative:
                chosen = candidates[i]
                break

        return chosen[0], chosen[1]

    def _pick_graze_patch(self, cx: float, cy: float,
                          awareness_r: float) -> tuple[float, float]:
        """Find the grass-richest small patch within GRAZE_PATCH_RADIUS of the herd center.
        Returns the tile coords of the best patch center, or (cx, cy) if no grass found."""
        if self._grid is None:
            return cx, cy
        grid = self._grid
        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        ic, ir = int(cx), int(cy)
        scan = min(GRAZE_PATCH_RADIUS, int(awareness_r))
        best_score = -1.0
        best_c, best_r = ic, ir

        for dr in range(-scan, scan + 1):
            for dc in range(-scan, scan + 1):
                if dr * dr + dc * dc > scan * scan:
                    continue
                r, c = ir + dr, ic + dc
                if not (0 <= r < rows and 0 <= c < cols):
                    continue
                # Count grass in a 3×3 window around candidate
                grass = 0
                for ddr in range(-1, 2):
                    for ddc in range(-1, 2):
                        rr, cc = r + ddr, c + ddc
                        if 0 <= rr < rows and 0 <= cc < cols and grid[rr][cc] == GRASS:
                            grass += 1
                if grass == 0:
                    continue
                # Prefer patches closer to herd center (distance penalty)
                dist_norm = math.sqrt(dr * dr + dc * dc) / max(1, scan)
                score = grass - dist_norm * 3.0
                if score > best_score:
                    best_score = score
                    best_c, best_r = c, r

        return float(best_c) + 0.5, float(best_r) + 0.5

    def _count_grass_fraction_in_awareness(self, cx: float, cy: float,
                                           awareness_r: float) -> float:
        """Return the fraction of land tiles within awareness_r that are grass.
        Returns 1.0 (no concern) when grid is unavailable."""
        if self._grid is None:
            return 1.0
        rows = len(self._grid)
        cols = len(self._grid[0]) if rows else 0
        ic, ir = int(cx), int(cy)
        r = max(1, int(awareness_r))
        land = grass = 0
        r_sq = r * r
        for dr in range(-r, r + 1):
            ro = ir + dr
            if not (0 <= ro < rows):
                continue
            for dc in range(-r, r + 1):
                if dr * dr + dc * dc > r_sq:
                    continue
                co = ic + dc
                if not (0 <= co < cols):
                    continue
                t = self._grid[ro][co]
                if t == WATER or t == SAND:
                    continue
                land += 1
                if t == GRASS:
                    grass += 1
        if land == 0:
            return 1.0
        return grass / land

    # ------------------------------------------------------------------
    # Wolf threat response
    # ------------------------------------------------------------------

    def _apply_wolf_threat(self, animals: list, wolves: list):
        """
        Each sheep individually checks whether any wolf is within
        SHEEP_WOLF_AWARENESS_R tiles.  If ≥ SHEEP_WOLF_SPOOK_FRAC of the herd
        is spooked, the herd flees to a grass patch far from wolves and corpses.
        """
        from wolf import Wolf as _Wolf, WOLF_SCARE_DURATION as _SCARE_DUR

        # All living wolves — any wolf is a threat, not just hunting ones
        wolf_positions = [(w.tx, w.ty) for w in wolves
                          if w.alive and w.dead_state is None]
        if not wolf_positions:
            return

        # Corpse positions to steer the flee target away from
        corpse_positions = [(c.tx, c.ty)
                            for c in getattr(self, '_recent_corpses', [])
                            if getattr(c, 'dead_state', None) is not None]

        awareness_sq = SHEEP_WOLF_AWARENESS_R ** 2

        # Group living sheep by herd
        by_herd: dict[int, list] = {}
        for a in animals:
            if a.herd_id >= 0:
                by_herd.setdefault(a.herd_id, []).append(a)

        for hid, members in by_herd.items():
            data = self._herds.get(hid)
            if data is None:
                continue
            cx, cy = data.cx, data.cy

            # Per-sheep awareness: find nearest threatening wolf for each sheep
            nearest_wolf_sq = float('inf')
            nearest_wx, nearest_wy = cx, cy

            for a in members:
                for wx, wy in wolf_positions:
                    d_sq = (wx - a.tx) ** 2 + (wy - a.ty) ** 2
                    if d_sq <= awareness_sq:
                        # This sheep spots a wolf — mark it spooked
                        a.wolf_aware       = True
                        a._wolf_fear_timer = max(getattr(a, '_wolf_fear_timer', 0.0),
                                                 _SCARE_DUR)
                        dd = math.sqrt(d_sq)
                        if dd > 0.001:
                            a.wolf_flee_dx = (a.tx - wx) / dd
                            a.wolf_flee_dy = (a.ty - wy) / dd
                        if d_sq < nearest_wolf_sq:
                            nearest_wolf_sq = d_sq
                            nearest_wx, nearest_wy = wx, wy
                        break  # one close wolf is enough to spook this sheep

            # Count currently spooked sheep in this herd
            spooked = sum(1 for a in members if getattr(a, 'wolf_aware', False))
            if spooked == 0:
                continue

            spook_frac = spooked / len(members)
            if spook_frac < SHEEP_WOLF_SPOOK_FRAC:
                continue   # not enough sheep alarmed yet

            # Enough sheep are spooked — trigger a flee migration if not already fleeing
            if data.state == _HerdData.MIGRATING:
                continue

            avoid_pts = list(wolf_positions) + corpse_positions
            mtx, mty  = self._pick_migration_target(
                cx, cy, data.awareness_r,
                flee_mult=2.5,
                avoid_positions=avoid_pts,
                min_dist=SHEEP_FLEE_MIN_DIST,
            )
            data.mtx       = mtx
            data.mty       = mty
            data.state     = _HerdData.MIGRATING
            data.timer     = 60.0
            data.emergency = True

    # ------------------------------------------------------------------
    # Per-frame herd state machine + attribute injection
    # ------------------------------------------------------------------

    def _update_herds(self, dt: float, animals: list, corpses: list = None):
        if corpses is None:
            corpses = []
        self._recent_corpses = corpses   # expose for _apply_wolf_threat

        # Group by herd id
        by_herd: dict[int, list] = {}
        for a in animals:
            if a.herd_id >= 0:
                by_herd.setdefault(a.herd_id, []).append(a)

        scan_sq = DEATH_SCAN_RADIUS ** 2

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

            # Compute awareness radius every frame
            awareness_r = self._herd_awareness_radius(cx, cy, members)
            data.awareness_r = awareness_r

            # --- Death-panic: count corpses near the herd center ---
            nearby_deaths = sum(
                1 for c in corpses
                if (c.tx - cx) ** 2 + (c.ty - cy) ** 2 <= scan_sq
            )
            death_ratio        = nearby_deaths / max(1, n)
            panic              = math.exp(death_ratio * DEATH_PANIC_SCALE) - 1.0
            data.nearby_deaths = nearby_deaths
            data.panic_level   = panic
            data.flee_dist_mult = 1.0 + min(panic, 3.0) * (DEATH_FLEE_DIST_MULT / 3.0)

            # Social cohesion target = raw center of mass (NOT snapped to grass).
            # Snapping to grass made the social center food-driven and unstable.
            # Grass-seeking is now handled separately via the grazing patch below.
            eff_cx, eff_cy = cx, cy

            # Refresh shared grazing patch periodically
            data.graze_refresh -= dt
            if data.graze_refresh <= 0:
                data.graze_cx, data.graze_cy = self._pick_graze_patch(cx, cy, awareness_r)
                data.graze_refresh = GRAZE_REFRESH_TIME
            # Initialise graze patch on first frame (both coords are 0.0)
            if data.graze_cx == 0.0 and data.graze_cy == 0.0:
                data.graze_cx, data.graze_cy = self._pick_graze_patch(cx, cy, awareness_r)

            # --- State machine ---
            data.timer -= dt

            if data.state == _HerdData.IDLE:
                # Death panic: cut idle timer exponentially
                if panic >= DEATH_PANIC_THRESHOLD:
                    max_wait = GATHER_DURATION / (1.0 + panic * 2.0)
                    data.timer = min(data.timer, max_wait)

                # Primary migration trigger: grass scarcity within awareness radius
                grass_frac = self._count_grass_fraction_in_awareness(cx, cy, awareness_r)
                if grass_frac < GRASS_MIGRATE_THRESHOLD and n >= 2:
                    data.state     = _HerdData.GATHERING
                    data.timer     = GATHER_DURATION * 0.5
                    data.emergency = True
                elif avg_h >= HUNGER_MIGRATE_THRESHOLD and n >= 2:
                    data.state     = _HerdData.GATHERING
                    data.timer     = GATHER_DURATION * 0.4
                    data.emergency = True
                elif data.timer <= 0:
                    # Fallback time-based migration (very rare)
                    data.state     = _HerdData.GATHERING
                    data.timer     = GATHER_DURATION * max(0.3, 1.0 / (1.0 + panic))
                    data.emergency = panic >= DEATH_PANIC_THRESHOLD

            elif data.state == _HerdData.GATHERING:
                if data.timer <= 0:
                    data.mtx, data.mty = self._pick_migration_target(
                        data.cx, data.cy, awareness_r, flee_mult=data.flee_dist_mult)
                    data.state = _HerdData.MIGRATING
                    data.timer = random.uniform(MIGRATION_DURATION_MIN,
                                                MIGRATION_DURATION_MAX)

            elif data.state == _HerdData.MIGRATING:
                end_condition = (data.timer <= 0 or
                                 (data.emergency and avg_h < HUNGER_MIGRATE_END
                                  and panic < DEATH_PANIC_THRESHOLD))
                if end_condition:
                    data.state     = _HerdData.IDLE
                    data.timer     = random.uniform(MIGRATION_INTERVAL_MIN,
                                                    MIGRATION_INTERVAL_MAX)
                    data.emergency = False
                    data.flee_dist_mult = 1.0

            # --- Push attributes onto each member ---
            gathering = (data.state == _HerdData.GATHERING)
            migrating = (data.state == _HerdData.MIGRATING)

            for a in members:
                a.herd_cx          = eff_cx
                a.herd_cy          = eff_cy
                a.herd_graze_cx    = data.graze_cx
                a.herd_graze_cy    = data.graze_cy
                a.herd_awareness_r = awareness_r
                a.migration_mode   = migrating
                a.migrate_tx       = data.mtx
                a.migrate_ty       = data.mty

                if gathering:
                    a.herd_pull_strength = GATHER_PULL_BOOST
                else:
                    age_frac = min(1.0, a.age / MATURITY_GRAVITY_AGE)
                    a.herd_pull_strength = (GRAVITY_YOUNG
                                            + (GRAVITY_MATURE - GRAVITY_YOUNG) * age_frac)
