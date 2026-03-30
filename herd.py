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

# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------

HERD_PROXIMITY      = 22.0   # tiles — max distance to be in the same herd
REASSIGN_INTERVAL   = 10.0   # sim-seconds between flood-fill reassignments
CURIOSITY_SWITCH    = 0.08   # base defection probability per reassignment cycle

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

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def update(self, dt: float, animals: list):
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
                    angle    = random.uniform(0, 2 * math.pi)
                    data.mdx = math.cos(angle)
                    data.mdy = math.sin(angle)
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
                a.herd_cx  = cx
                a.herd_cy  = cy
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
