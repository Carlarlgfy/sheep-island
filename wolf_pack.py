"""
WolfPackManager — proximity-based pack grouping with shared hunt coordination.

Each wolf needs:
    .tx, .ty        float  — tile position
    .pack_id        int    — assigned here; -1 = lone wolf
    .alive          bool
    .dead_state     None | "fresh" | "decayed"

WolfPackManager writes these onto each wolf every frame:
    .pack_id            int    — pack membership
    .pack_cx, .pack_cy  float  — pack center (tile coords)
    .pack_size          int    — number of living pack members
    .pack_hunt_target   Sheep | None  — shared hunt target for the pack
"""

import math
import random

# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------

PACK_PROXIMITY      = 22.0   # tiles — max distance to share a pack
REASSIGN_INTERVAL   = 8.0    # sim-seconds between proximity reassignments

# Hunt coordination
PACK_TARGET_SCAN_INTERVAL = 8.0    # seconds between pack-level prey rescans
PACK_TARGET_RADIUS        = 600.0  # tile radius for pack-level prey (hearing range)
PACK_SMELL_RADIUS         = 1000.0 # tile radius for pack-level corpse detection

# Wolf threat alarm pushed to herd manager (tile radius around pack center)
WOLF_ALARM_RADIUS   = 45.0

# Pack awareness radius (written to each wolf as pack_awareness_radius)
PACK_AWARENESS_BASE     = 30.0   # tiles — baseline for a lone wolf
PACK_AWARENESS_PER_WOLF = 2.5    # extra tiles per additional pack member
PACK_AWARENESS_MAX      = 65.0   # cap


# ---------------------------------------------------------------------------
# Per-pack state
# ---------------------------------------------------------------------------

class _PackData:
    __slots__ = ("cx", "cy", "size", "hunt_target", "scan_timer", "awareness_radius")

    def __init__(self):
        self.cx               = 0.0
        self.cy               = 0.0
        self.size             = 0
        self.hunt_target      = None   # shared prey target
        self.scan_timer       = random.uniform(0, PACK_TARGET_SCAN_INTERVAL)
        self.awareness_radius = PACK_AWARENESS_BASE


# ---------------------------------------------------------------------------
# WolfPackManager
# ---------------------------------------------------------------------------

class WolfPackManager:
    """
    Two-phase update:
      1. Every REASSIGN_INTERVAL: flood-fill proximity grouping.
      2. Every frame: compute pack center, coordinate hunt target,
         push pack_id / pack_cx / pack_cy / pack_size / pack_hunt_target
         onto each wolf.
    """

    def __init__(self):
        self._timer  = 0.0
        self._packs: dict[int, _PackData] = {}

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def update(self, dt: float, wolves: list, sheep_list: list):
        """Call once per frame from main.py."""
        living = [w for w in wolves
                  if w.alive and w.dead_state is None]

        self._timer -= dt
        if self._timer <= 0:
            self._timer = REASSIGN_INTERVAL
            if living:
                self._reassign(living)

        if living:
            self._update_packs(dt, living, sheep_list)

    # ------------------------------------------------------------------
    # Reassignment
    # ------------------------------------------------------------------

    def _reassign(self, wolves: list):
        cell = PACK_PROXIMITY

        # Spatial hash
        spatial: dict[tuple, list] = {}
        for w in wolves:
            key = (int(w.tx / cell), int(w.ty / cell))
            spatial.setdefault(key, []).append(w)

        old_ids = {id(w): w.pack_id for w in wolves}

        for w in wolves:
            w.pack_id = -1

        pack_id = 0
        for seed in wolves:
            if seed.pack_id != -1:
                continue
            seed.pack_id = pack_id
            stack = [seed]
            while stack:
                w = stack.pop()
                cx, cy = int(w.tx / cell), int(w.ty / cell)
                for dcx in (-1, 0, 1):
                    for dcy in (-1, 0, 1):
                        for cand in spatial.get((cx + dcx, cy + dcy), []):
                            if cand.pack_id != -1:
                                continue
                            ddx = cand.tx - w.tx
                            ddy = cand.ty - w.ty
                            if ddx * ddx + ddy * ddy <= cell * cell:
                                cand.pack_id = pack_id
                                stack.append(cand)
            pack_id += 1

        # Build new pack dict, carry over state
        new_packs: dict[int, _PackData] = {}
        for w in wolves:
            if w.pack_id not in new_packs:
                new_packs[w.pack_id] = _PackData()

        # Transfer hunt_target from old pack if most members moved together
        old_to_votes: dict[int, list] = {}
        for w in wolves:
            old_id = old_ids.get(id(w), -1)
            if old_id >= 0:
                old_to_votes.setdefault(old_id, []).append(w.pack_id)

        transferred: set = set()
        for old_id, votes in old_to_votes.items():
            if old_id not in self._packs:
                continue
            best_new = max(set(votes), key=votes.count)
            if best_new in new_packs and best_new not in transferred:
                old_data = self._packs[old_id]
                nd = new_packs[best_new]
                nd.hunt_target = old_data.hunt_target
                nd.scan_timer  = old_data.scan_timer
                transferred.add(best_new)

        self._packs = new_packs

    # ------------------------------------------------------------------
    # Per-frame pack update
    # ------------------------------------------------------------------

    def _update_packs(self, dt: float, wolves: list, sheep_list: list):
        by_pack: dict[int, list] = {}
        for w in wolves:
            if w.pack_id >= 0:
                by_pack.setdefault(w.pack_id, []).append(w)

        scan_r_sq = PACK_TARGET_RADIUS ** 2

        for pid, members in by_pack.items():
            data = self._packs.get(pid)
            if data is None:
                data = _PackData()
                self._packs[pid] = data

            n  = len(members)
            cx = sum(w.tx for w in members) / n
            cy = sum(w.ty for w in members) / n
            data.cx               = cx
            data.cy               = cy
            data.size             = n
            data.awareness_radius = min(PACK_AWARENESS_MAX,
                                        PACK_AWARENESS_BASE + (n - 1) * PACK_AWARENESS_PER_WOLF)

            # Validate shared hunt target
            # A fresh corpse with meat is still a valid target; only discard when
            # the animal is dead AND not a useful fresh corpse.
            ht = data.hunt_target
            if ht is not None:
                is_fresh_corpse = (ht.dead_state == "fresh"
                                   and getattr(ht, 'meat_value', 0.0) > 0)
                if not is_fresh_corpse and (not ht.alive or ht.dead_state is not None):
                    data.hunt_target = None
                    ht = None

            # Periodic pack-level prey scan
            data.scan_timer -= dt
            if data.scan_timer <= 0:
                data.scan_timer = PACK_TARGET_SCAN_INTERVAL
                # Only scan when at least one member is hungry enough to hunt
                any_hungry = any(w.hunger >= 0.45 for w in members)
                if any_hungry or ht is None:
                    best = self._find_pack_prey(cx, cy, sheep_list, scan_r_sq)
                    if best is not None:
                        data.hunt_target = best

            # Push attributes onto all pack members
            for w in members:
                w.pack_cx               = data.cx
                w.pack_cy               = data.cy
                w.pack_size             = data.size
                w.pack_hunt_target      = data.hunt_target
                w.pack_awareness_radius = data.awareness_radius

    # ------------------------------------------------------------------
    # Pack-level prey selection
    # ------------------------------------------------------------------

    def _find_pack_prey(self, cx: float, cy: float,
                        sheep_list: list, scan_r_sq: float):
        """Best target for the pack: fresh corpses first, then live prey via hearing.

        Corpses are detectable at PACK_SMELL_RADIUS (1000 tiles).
        Live moving prey are detectable at PACK_TARGET_RADIUS (600 tiles, hearing).
        Live stationary prey are detectable at 150 tiles (passive scent).
        """
        smell_sq  = PACK_SMELL_RADIUS ** 2
        scent_sq  = 150.0 ** 2   # stationary live prey

        # --- Corpse priority ---
        best_corpse_dist = float('inf')
        best_corpse      = None
        for sheep in sheep_list:
            if sheep.dead_state != "fresh":
                continue
            if getattr(sheep, 'meat_value', 0.0) <= 0:
                continue
            ddx     = sheep.tx - cx
            ddy     = sheep.ty - cy
            dist_sq = ddx * ddx + ddy * ddy
            if dist_sq <= smell_sq and dist_sq < best_corpse_dist:
                best_corpse_dist = dist_sq
                best_corpse      = sheep
        if best_corpse is not None:
            return best_corpse

        # --- Live prey (hearing for moving, passive scent for stationary) ---
        best_score   = -1.0
        best_target  = None
        best_dist_sq = float('inf')

        for sheep in sheep_list:
            if sheep.dead_state is not None or not sheep.alive:
                continue
            ddx     = sheep.tx - cx
            ddy     = sheep.ty - cy
            dist_sq = ddx * ddx + ddy * ddy

            is_moving = (abs(getattr(sheep, 'dx', 0.0)) + abs(getattr(sheep, 'dy', 0.0))) > 0.01
            limit_sq  = scan_r_sq if is_moving else scent_sq
            if dist_sq > limit_sq:
                continue

            score = 0.0
            if hasattr(sheep, 'maturity_age'):
                if sheep.age < sheep.maturity_age:
                    score += 3.0
                elif sheep.age > getattr(sheep, 'lifespan', 99999) * 0.70:
                    score += 2.0
            max_hp = float(getattr(sheep, 'genetic_hp', 1))
            if max_hp > 0:
                hp_frac = sheep.hp / max_hp
                if hp_frac < 0.30:
                    score += 4.0
                elif hp_frac < 0.60:
                    score += 1.5

            if score > best_score or (score == best_score and dist_sq < best_dist_sq):
                best_score   = score
                best_target  = sheep
                best_dist_sq = dist_sq

        return best_target

    # ------------------------------------------------------------------
    # Wolf alarm radius (used by herd.py to trigger sheep flee)
    # ------------------------------------------------------------------

    def get_active_threats(self) -> list:
        """Return list of (cx, cy) for every pack that has a hunt target."""
        threats = []
        for pid, data in self._packs.items():
            if data.hunt_target is not None:
                threats.append((data.cx, data.cy))
        return threats
