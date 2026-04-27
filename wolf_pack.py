"""
WolfPackManager — proximity-based pack grouping with shared hunt coordination
and dynamic territory control.

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

from wolf import WOLF_HUNGER_HUNT
from mapgen import is_walkable_tile

# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------

PACK_PROXIMITY      = 36.0   # tiles — packs should stay one pack unless they truly drift apart
REASSIGN_INTERVAL   = 4.0    # sim-seconds between proximity reassignments
OPTIMAL_PACK_SIZE   = 10
MAX_PACK_SIZE       = int(OPTIMAL_PACK_SIZE * 1.5)
PACK_REASSEMBLE_RADIUS = 28.0

# Hunt coordination
PACK_TARGET_SCAN_INTERVAL = 5.0    # faster prey rescans (was 8s)
PACK_TARGET_RADIUS        = 600.0  # tile radius for pack-level prey (hearing range)
PACK_SMELL_RADIUS         = 1000.0 # tile radius for pack-level corpse detection
PACK_FEAST_MIN            = 22.0   # feast window — 15-30s visible feast, then camp
PACK_FEAST_MAX            = 50.0
PACK_CHILL_MIN            = 400.0  # ~1.3 days rest (was 2 days)
PACK_CHILL_MAX            = 800.0  # ~2.7 days rest (was 4 days)
PACK_FEAST_CORPSE_RADIUS  = 50.0   # tile radius — detect fresh kill near pack to enter feast
PACK_TRAVEL_STEP_MIN      = 60.0
PACK_TRAVEL_STEP_MAX      = 140.0
PACK_TRAVEL_RADIUS_MIN    = 40.0
PACK_TRAVEL_RADIUS_MAX    = 180.0
PACK_REST_HUNGER_MAX      = 0.24
PACK_HUNT_TRIGGER_AVG     = 0.42
PACK_SOLO_RANK_FRAC       = 0.60
PACK_SOLO_HUNGER_TRIGGER  = 0.58
PACK_SOLO_REPUTATION_MAX  = 0.38
PACK_SOLO_FORCE_HUNGER    = 0.92
PACK_COUPLE_FORM_RADIUS   = 18.0

# Wolf threat alarm pushed to herd manager (tile radius around pack center)
WOLF_ALARM_RADIUS   = 45.0

# Pack awareness radius (written to each wolf as pack_awareness_radius)
PACK_AWARENESS_BASE     = 26.0
PACK_AWARENESS_PER_WOLF = 2.0
PACK_AWARENESS_MAX      = 52.0

PACK_MALE_SOFT_CAP_FRAC   = 0.30
PACK_TARGET_MALE_FRAC     = 0.30
PACK_TARGET_FEMALE_FRAC   = 0.70
PACK_INSTABILITY_SIZE     = MAX_PACK_SIZE
PACK_FIGHT_CHANCE         = 0.007  # real wolves rarely fight to the death within the pack
PACK_SPLIT_PUSH           = 2.2
PACK_BETA_CHANCE          = 0.40   # submission is the most common conflict outcome
PACK_EXILE_CHANCE         = 0.56   # exile is common for persistent challengers
PACK_DEATH_CHANCE         = 0.04   # intra-pack killing is rare — wolves know their packmates
PACK_FEMALE_EXCESS_FRAC   = 0.85
PACK_MALE_EXCESS_FRAC     = 0.41
PACK_RATIO_FIGHT_INTERVAL = 45.0
PACK_RATIO_FIGHT_DAMAGE   = (2.0, 5.0)
PACK_CHALLENGE_INTERVAL   = 70.0
PACK_CHALLENGE_DAMAGE     = (2.5, 5.5)
PACK_TERRITORY_BASE_RADIUS = 120.0
PACK_TERRITORY_PER_WOLF    = 16.0
PACK_TERRITORY_MIN_RADIUS  = 90.0
PACK_TERRITORY_MAX_RADIUS  = 260.0
PACK_TERRITORY_CELL_SIZE   = 24.0
PACK_TERRITORY_HEAT_DECAY  = 0.035
PACK_TERRITORY_HEAT_HOME   = 3.0
PACK_TERRITORY_HEAT_MEMBER = 1.1
PACK_TERRITORY_HEAT_HUNT   = 2.2
PACK_TERRITORY_GROW_LIMIT  = 6
PACK_TERRITORY_PRUNE_HEAT  = 0.45
PACK_HOME_BASE_RADIUS      = 26.0
PACK_HOME_PER_WOLF         = 2.8
PACK_TERRITORY_SEARCH_STEP = 60.0
PACK_TERRITORY_REEVAL_MIN  = 75.0
PACK_TERRITORY_REEVAL_MAX  = 150.0
PACK_TERRITORY_RESOURCE_LOW = 2.0
PACK_TERRITORY_EXPAND_STEP  = 16.0
PACK_TERRITORY_CONFLICT_INTERVAL = 55.0
PACK_TERRITORY_CONFLICT_DAMAGE   = (2.5, 6.5)
PACK_TERRITORY_OVERLAP_PAD       = 16.0
PACK_EXILE_AVOID_HOME_RADIUS     = 80.0
PACK_COUPLE_BOND_CHANCE          = 0.60


# ---------------------------------------------------------------------------
# Per-pack state
# ---------------------------------------------------------------------------

class _PackData:
    __slots__ = ("cx", "cy", "size", "hunt_target", "scan_timer",
                 "awareness_radius", "mode", "chill_timer", "alpha_id",
                 "feast_timer", "camp_timer", "camp_x", "camp_y",
                 "meal_corpse_id", "blocked_corpse_id", "travel_x",
                 "travel_y", "travel_timer", "resting", "rest_timer",
                 "ratio_fight_timer", "mate_challenge_timer",
                 "territory_cx", "territory_cy", "territory_radius",
                 "home_x", "home_y", "home_radius", "resource_score",
                 "territory_quality", "territory_timer",
                 "territory_conflict_timer", "territory_cells",
                 "territory_heat", "home_cell")

    HUNT  = "hunt"
    FEAST = "feast"
    CHILL = "chill"
    CAMP  = "camp"

    def __init__(self):
        self.cx               = 0.0
        self.cy               = 0.0
        self.size             = 0
        self.hunt_target      = None   # shared prey target
        self.scan_timer       = random.uniform(0, PACK_TARGET_SCAN_INTERVAL)
        self.awareness_radius = PACK_AWARENESS_BASE
        self.mode             = _PackData.CHILL
        self.chill_timer      = random.uniform(PACK_CHILL_MIN * 0.35, PACK_CHILL_MAX * 0.35)
        self.alpha_id         = None
        self.feast_timer      = 0.0
        self.camp_timer       = 0.0
        self.camp_x           = 0.0
        self.camp_y           = 0.0
        self.meal_corpse_id   = None
        self.blocked_corpse_id = None
        self.travel_x         = 0.0
        self.travel_y         = 0.0
        self.travel_timer     = 0.0
        self.resting          = True
        self.rest_timer       = self.chill_timer
        self.ratio_fight_timer = random.uniform(PACK_RATIO_FIGHT_INTERVAL * 0.35,
                                                PACK_RATIO_FIGHT_INTERVAL)
        self.mate_challenge_timer = random.uniform(PACK_CHALLENGE_INTERVAL * 0.35,
                                                   PACK_CHALLENGE_INTERVAL)
        self.territory_cx     = 0.0
        self.territory_cy     = 0.0
        self.territory_radius = PACK_TERRITORY_BASE_RADIUS
        self.home_x           = 0.0
        self.home_y           = 0.0
        self.home_radius      = PACK_HOME_BASE_RADIUS
        self.resource_score   = 0.0
        self.territory_quality = 0.0
        self.territory_timer  = random.uniform(PACK_TERRITORY_REEVAL_MIN * 0.35,
                                               PACK_TERRITORY_REEVAL_MAX * 0.75)
        self.territory_conflict_timer = random.uniform(PACK_TERRITORY_CONFLICT_INTERVAL * 0.35,
                                                       PACK_TERRITORY_CONFLICT_INTERVAL)
        self.territory_cells  = set()
        self.territory_heat   = {}
        self.home_cell        = (0, 0)


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

    def update(self, dt: float, wolves: list, sheep_list: list, grid: list | None = None):
        """Call once per frame from main.py."""
        living = [w for w in wolves
                  if w.alive and w.dead_state is None]

        if living:
            self._update_lone_bonds(living)
            self._apply_pack_politics(dt, living)

        self._timer -= dt
        if self._timer <= 0:
            self._timer = REASSIGN_INTERVAL
            if living:
                self._reassign(living)
                self._reassemble_packs(living)
                self._split_oversized_packs(living)

        if living:
            self._update_packs(dt, living, sheep_list, grid)

    # ------------------------------------------------------------------
    # Reassignment
    # ------------------------------------------------------------------

    def _reassign(self, wolves: list):
        cell = PACK_PROXIMITY

        # Spatial hash
        spatial: dict[tuple, list] = {}
        for w in wolves:
            if getattr(w, "true_loner", False) or getattr(w, "solo_excursion", False):
                continue
            key = (int(w.tx / cell), int(w.ty / cell))
            spatial.setdefault(key, []).append(w)

        old_ids = {id(w): w.pack_id for w in wolves}

        for w in wolves:
            w.pack_id = -1

        pack_id = 0
        for seed in wolves:
            if getattr(seed, "true_loner", False) or getattr(seed, "solo_excursion", False):
                continue
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
                            if not self._can_share_pack(w, cand):
                                continue
                            ddx = cand.tx - w.tx
                            ddy = cand.ty - w.ty
                            if (ddx * ddx + ddy * ddy <= cell * cell
                                    and self._social_affinity(w, cand) >= 0.30):
                                cand.pack_id = pack_id
                                stack.append(cand)
            pack_id += 1

        # Build new pack dict, carry over state
        new_packs: dict[int, _PackData] = {}
        for w in wolves:
            if w.pack_id >= 0 and w.pack_id not in new_packs:
                new_packs[w.pack_id] = _PackData()

        # Transfer hunt_target from old pack if most members moved together
        old_to_votes: dict[int, list] = {}
        for w in wolves:
            old_id = old_ids.get(id(w), -1)
            if old_id >= 0 and w.pack_id >= 0:
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
                nd.mode        = old_data.mode
                nd.chill_timer = old_data.chill_timer
                nd.feast_timer = old_data.feast_timer
                nd.alpha_id    = old_data.alpha_id
                nd.camp_timer  = old_data.camp_timer
                nd.camp_x      = old_data.camp_x
                nd.camp_y      = old_data.camp_y
                nd.meal_corpse_id = old_data.meal_corpse_id
                nd.blocked_corpse_id = old_data.blocked_corpse_id
                nd.travel_x    = old_data.travel_x
                nd.travel_y    = old_data.travel_y
                nd.travel_timer = old_data.travel_timer
                nd.resting     = old_data.resting
                nd.rest_timer  = old_data.rest_timer
                nd.ratio_fight_timer = old_data.ratio_fight_timer
                nd.mate_challenge_timer = old_data.mate_challenge_timer
                nd.territory_cx = old_data.territory_cx
                nd.territory_cy = old_data.territory_cy
                nd.territory_radius = old_data.territory_radius
                nd.home_x = old_data.home_x
                nd.home_y = old_data.home_y
                nd.home_radius = old_data.home_radius
                nd.resource_score = old_data.resource_score
                nd.territory_quality = old_data.territory_quality
                nd.territory_timer = old_data.territory_timer
                nd.territory_conflict_timer = old_data.territory_conflict_timer
                nd.territory_cells = set(old_data.territory_cells)
                nd.territory_heat = dict(old_data.territory_heat)
                nd.home_cell = old_data.home_cell
                transferred.add(best_new)

        self._packs = new_packs

    def _reassemble_packs(self, wolves: list):
        by_pack: dict[int, list] = {}
        for wolf in wolves:
            if wolf.pack_id >= 0 and not getattr(wolf, "true_loner", False):
                by_pack.setdefault(wolf.pack_id, []).append(wolf)
        if len(by_pack) < 2:
            return

        centers: dict[int, tuple[float, float]] = {}
        for pid, members in by_pack.items():
            centers[pid] = (
                sum(w.tx for w in members) / len(members),
                sum(w.ty for w in members) / len(members),
            )

        changed = True
        reassemble_sq = PACK_REASSEMBLE_RADIUS ** 2
        while changed:
            changed = False
            pack_ids = list(by_pack.keys())
            for i, p1 in enumerate(pack_ids):
                if p1 not in by_pack:
                    continue
                m1 = by_pack[p1]
                if len(m1) >= OPTIMAL_PACK_SIZE:
                    continue
                c1x, c1y = centers[p1]
                best = None
                best_score = float("inf")
                for p2 in pack_ids[i + 1:]:
                    if p2 not in by_pack:
                        continue
                    m2 = by_pack[p2]
                    if any(getattr(w, "pair_bond_only", False) for w in m1 + m2):
                        continue
                    if len(m1) + len(m2) > MAX_PACK_SIZE:
                        continue
                    c2x, c2y = centers[p2]
                    d_sq = (c1x - c2x) ** 2 + (c1y - c2y) ** 2
                    if d_sq > reassemble_sq:
                        continue
                    social = 0.0
                    pairs = 0
                    for a in m1[:4]:
                        for b in m2[:4]:
                            social += self._social_affinity(a, b)
                            pairs += 1
                    score = d_sq - (social / max(1, pairs)) * 18.0
                    if score < best_score:
                        best_score = score
                        best = p2
                if best is None:
                    continue
                for wolf in by_pack[best]:
                    wolf.pack_id = p1
                m1.extend(by_pack[best])
                del by_pack[best]
                centers[p1] = (
                    sum(w.tx for w in m1) / len(m1),
                    sum(w.ty for w in m1) / len(m1),
                )
                centers.pop(best, None)
                changed = True
                break

    def _split_oversized_packs(self, wolves: list):
        by_pack: dict[int, list] = {}
        for wolf in wolves:
            if wolf.pack_id >= 0 and not getattr(wolf, "true_loner", False):
                by_pack.setdefault(wolf.pack_id, []).append(wolf)
        if not by_pack:
            return

        next_id = max(by_pack) + 1
        for pid, members in list(by_pack.items()):
            n = len(members)
            if n <= MAX_PACK_SIZE:
                continue
            split_count = min(4, max(2, math.ceil(n / OPTIMAL_PACK_SIZE)))
            seeds = sorted(members, key=self._alpha_score, reverse=True)[:split_count]
            groups = {idx: [] for idx in range(split_count)}
            for wolf in members:
                best_idx = 0
                best_score = -1e9
                for idx, seed in enumerate(seeds):
                    score = self._social_affinity(wolf, seed)
                    score += wolf.genetic_similarity(seed) * 2.0
                    score -= math.hypot(wolf.tx - seed.tx, wolf.ty - seed.ty) * 0.05
                    if score > best_score:
                        best_score = score
                        best_idx = idx
                groups[best_idx].append(wolf)
            for idx, bucket in groups.items():
                new_id = pid if idx == 0 else next_id + idx - 1
                for wolf in bucket:
                    wolf.pack_id = new_id
            next_id += split_count - 1

    # ------------------------------------------------------------------
    # Per-frame pack update
    # ------------------------------------------------------------------

    def _update_packs(self, dt: float, wolves: list, sheep_list: list, grid: list | None):
        by_pack: dict[int, list] = {}
        for w in wolves:
            if w.pack_id >= 0:
                by_pack.setdefault(w.pack_id, []).append(w)

        for w in wolves:
            if w.pack_id < 0:
                w.pack_cx               = w.tx
                w.pack_cy               = w.ty
                w.pack_size             = 1
                w.pack_hunt_target      = None
                w.pack_awareness_radius = PACK_AWARENESS_BASE
                w.pack_mode             = _PackData.CHILL
                w.pack_mode_timer       = 0.0
                w.pack_alpha_id         = w.wolf_id
                w.pack_is_alpha         = True
                w.pack_rank             = 1
                w.pack_reputation       = 1.0
                w.pack_camp_x           = w.tx
                w.pack_camp_y           = w.ty
                w.pack_blocked_corpse_id = None
                w.pack_move_x           = w.tx
                w.pack_move_y           = w.ty
                w.pack_resting          = False
                w.pack_rest_timer       = 0.0
                w.pack_solo_hunt_ok     = False
                w.pack_territory_cx     = w.tx
                w.pack_territory_cy     = w.ty
                w.pack_territory_radius = PACK_TERRITORY_MIN_RADIUS
                w.pack_home_x           = w.tx
                w.pack_home_y           = w.ty
                w.pack_home_radius      = PACK_HOME_BASE_RADIUS

        scan_r_sq = PACK_TARGET_RADIUS ** 2

        for pid, members in by_pack.items():
            data = self._packs.get(pid)
            if data is None:
                data = _PackData()
                self._packs[pid] = data

            n  = len(members)
            alpha = max(members, key=self._alpha_score)
            ranked = sorted(members, key=self._dominance_score, reverse=True)
            avg_x = sum(w.tx for w in members) / max(1, n)
            avg_y = sum(w.ty for w in members) / max(1, n)
            data.alpha_id         = alpha.wolf_id
            data.cx               = avg_x
            data.cy               = avg_y
            data.size             = n
            data.awareness_radius = min(PACK_AWARENESS_MAX,
                                        PACK_AWARENESS_BASE + (n - 1) * PACK_AWARENESS_PER_WOLF)
            if data.travel_x == 0.0 and data.travel_y == 0.0:
                data.travel_x = data.cx
                data.travel_y = data.cy
            if data.territory_cx == 0.0 and data.territory_cy == 0.0:
                data.territory_cx = data.cx
                data.territory_cy = data.cy
                data.home_x = data.cx
                data.home_y = data.cy

            # Validate shared hunt target
            ht = data.hunt_target
            if ht is not None:
                if not ht.alive or ht.dead_state is not None:
                    data.hunt_target = None
                    ht = None

            feeding_members = [w for w in members if w.state == "eat" and w._hunt_target is not None]
            any_feasting = bool(feeding_members)
            feeding_corpse = feeding_members[0]._hunt_target if feeding_members else None
            feeding_corpse_id = id(feeding_corpse) if feeding_corpse is not None else None
            any_desperate = any(w.hunger >= 0.72 and getattr(w, "_meal_cooldown", 0.0) <= 0.0
                                for w in members)
            any_hungry = any(w.hunger >= 0.52 and getattr(w, "_meal_cooldown", 0.0) <= 0.0
                             for w in members)
            all_recently_fed = all(getattr(w, "_meal_cooldown", 0.0) > 0.0 for w in members)
            avg_hunger = sum(w.hunger for w in members) / max(1, n)
            pair_only_pack = n == 2 and all(getattr(w, "pair_bond_only", False) for w in members)

            # Detect fresh kill near pack center — break feast deadlock without needing
            # wolves to already be eating
            feast_r_sq = PACK_FEAST_CORPSE_RADIUS ** 2
            any_fresh_kill = any(
                getattr(s, 'dead_state', None) == "fresh"
                and getattr(s, 'meat_value', 0) > 0
                and id(s) != data.blocked_corpse_id
                and (s.tx - data.cx) ** 2 + (s.ty - data.cy) ** 2 <= feast_r_sq
                for s in sheep_list
            )

            if data.mode == _PackData.CAMP and data.camp_timer > 0.0 and not any_desperate:
                data.mode = _PackData.CAMP
                data.camp_timer = max(0.0, data.camp_timer - dt)
                data.hunt_target = None
                data.feast_timer = 0.0
            elif any_feasting:
                if data.mode != _PackData.FEAST or data.meal_corpse_id != feeding_corpse_id:
                    data.feast_timer = random.uniform(PACK_FEAST_MIN, PACK_FEAST_MAX)
                    data.meal_corpse_id = feeding_corpse_id
                    data.camp_x = alpha.tx
                    data.camp_y = alpha.ty
                else:
                    data.feast_timer = max(0.0, data.feast_timer - dt)
                if data.feast_timer > 0.0:
                    data.mode = _PackData.FEAST
                    data.chill_timer = 0.0
                else:
                    data.mode = _PackData.CAMP
                    data.hunt_target = None
                    data.camp_timer = random.uniform(PACK_CHILL_MIN, PACK_CHILL_MAX)
                    data.blocked_corpse_id = data.meal_corpse_id
            elif data.mode == _PackData.HUNT and any_fresh_kill:
                # A fresh kill is nearby — enter feast so pack wolves can converge and eat.
                # This breaks the deadlock where feast requires eating but eating requires feast.
                data.mode = _PackData.FEAST
                data.feast_timer = random.uniform(PACK_FEAST_MIN, PACK_FEAST_MAX)
                data.camp_x = data.cx
                data.camp_y = data.cy
                data.hunt_target = None
            elif data.mode == _PackData.FEAST:
                data.mode = _PackData.CAMP
                data.feast_timer = 0.0
                data.camp_timer = random.uniform(PACK_CHILL_MIN, PACK_CHILL_MAX)
                data.blocked_corpse_id = data.meal_corpse_id
                data.hunt_target = None
            elif data.chill_timer > 0.0 and not any_desperate:
                data.mode = _PackData.CHILL
                data.chill_timer = max(0.0, data.chill_timer - dt)
                data.hunt_target = None
            elif all_recently_fed:
                data.mode = _PackData.CHILL
                data.hunt_target = None
                data.feast_timer = 0.0
                data.camp_timer = 0.0
                data.chill_timer = max(data.chill_timer, min(
                    getattr(w, "_meal_cooldown", 0.0) for w in members
                ))
            else:
                should_group_hunt = (
                    any_desperate
                    or avg_hunger >= PACK_HUNT_TRIGGER_AVG
                    or (pair_only_pack and any_hungry)
                    or (any_hungry and n <= 3)
                )
                data.mode = _PackData.HUNT if should_group_hunt else _PackData.CHILL
                if data.mode == _PackData.CHILL:
                    data.hunt_target = None
                    data.feast_timer = 0.0
                    data.camp_timer = 0.0
                    data.chill_timer = max(data.chill_timer, random.uniform(PACK_CHILL_MIN * 0.35,
                                                                            PACK_CHILL_MAX * 0.55))
                else:
                    data.blocked_corpse_id = None

            if data.mode == _PackData.CAMP:
                data.resting = True
                data.rest_timer = data.camp_timer
            elif data.mode == _PackData.CHILL and avg_hunger <= PACK_REST_HUNGER_MAX:
                data.resting = True
                data.rest_timer = data.chill_timer
            else:
                data.resting = False
                data.rest_timer = 0.0

            # Periodic pack-level prey scan
            data.scan_timer -= dt
            if data.mode == _PackData.HUNT and data.scan_timer <= 0:
                data.scan_timer = PACK_TARGET_SCAN_INTERVAL
                best = self._find_pack_prey(data.territory_cx, data.territory_cy,
                                            sheep_list, scan_r_sq,
                                            territory_cells=data.territory_cells)
                if best is None and data.resource_score < PACK_TERRITORY_RESOURCE_LOW:
                    best = self._find_pack_prey(data.territory_cx, data.territory_cy,
                                                sheep_list, scan_r_sq,
                                                territory_cells=None)
                data.hunt_target = best

            self._update_pack_territory(dt, data, members, sheep_list, grid)

        self._resolve_territory_conflicts(dt, by_pack)

        for pid, members in by_pack.items():
            data = self._packs.get(pid)
            if data is None:
                continue
            self._update_pack_travel(dt, data, members)

            # Push attributes onto all pack members
            n = len(members)
            pair_only_pack = n == 2 and all(getattr(w, "pair_bond_only", False) for w in members)
            ranked = sorted(members, key=self._dominance_score, reverse=True)
            worst_rank_threshold = max(2, math.ceil(n * PACK_SOLO_RANK_FRAC))
            for rank_idx, w in enumerate(ranked, start=1):
                denom = max(1, n - 1)
                reputation = 1.0 - ((rank_idx - 1) / denom)
                rest_of_pack_hunger = (
                    (sum(m.hunger for m in members if m is not w) / max(1, n - 1))
                    if n > 1 else w.hunger
                )
                hungry_low_rank = (
                    not pair_only_pack
                    and rank_idx >= worst_rank_threshold
                    and w.hunger >= PACK_SOLO_HUNGER_TRIGGER
                    and rest_of_pack_hunger <= PACK_REST_HUNGER_MAX
                )
                isolated_by_status = (
                    not pair_only_pack
                    and reputation <= PACK_SOLO_REPUTATION_MAX
                    and w.hunger >= WOLF_HUNGER_HUNT
                )
                forced_solo = w.hunger >= PACK_SOLO_FORCE_HUNGER or (
                    w.hunger >= 1.0 and w.hp < w.max_hp * 0.90
                )

                w.pack_cx               = data.cx
                w.pack_cy               = data.cy
                w.pack_size             = data.size
                w.pack_hunt_target      = data.hunt_target
                w.pack_awareness_radius = data.awareness_radius
                w.pack_mode             = data.mode
                w.pack_mode_timer       = data.camp_timer if data.mode == _PackData.CAMP else data.chill_timer
                w.pack_alpha_id         = data.alpha_id
                w.pack_is_alpha         = (w.wolf_id == data.alpha_id)
                w.pack_rank             = rank_idx
                w.pack_reputation       = reputation
                w.pack_camp_x           = data.camp_x if data.mode == _PackData.CAMP else data.cx
                w.pack_camp_y           = data.camp_y if data.mode == _PackData.CAMP else data.cy
                w.pack_blocked_corpse_id = data.blocked_corpse_id
                w.pack_move_x           = data.travel_x
                w.pack_move_y           = data.travel_y
                w.pack_resting          = data.resting and w.hunger <= max(PACK_REST_HUNGER_MAX, WOLF_HUNGER_HUNT)
                w.pack_rest_timer       = data.rest_timer
                w.pack_territory_cx     = data.territory_cx
                w.pack_territory_cy     = data.territory_cy
                w.pack_territory_radius = data.territory_radius
                w.pack_home_x           = data.home_x
                w.pack_home_y           = data.home_y
                w.pack_home_radius      = data.home_radius
                w.pack_solo_hunt_ok     = (
                    (hungry_low_rank or isolated_by_status or forced_solo)
                    and data.mode != _PackData.FEAST
                )

    def _social_affinity(self, a, b) -> float:
        score = 0.0
        if getattr(a, "pair_bond_only", False) or getattr(b, "pair_bond_only", False):
            if getattr(a, "mate_bond_id", None) == getattr(b, "wolf_id", None):
                return 8.0
            if a.related_to(b):
                return 5.0
            return -10.0
        if (getattr(a, "wary_of_wolf_ids", None) and b.wolf_id in a.wary_of_wolf_ids):
            return -8.0
        if (getattr(b, "wary_of_wolf_ids", None) and a.wolf_id in b.wary_of_wolf_ids):
            return -8.0
        if getattr(a, "mate_bond_id", None) == getattr(b, "wolf_id", None):
            score += 4.0
        if a.related_to(b):
            score += 2.5
        score += a.genetic_similarity(b) * 1.5
        if a.pack_id >= 0 and a.pack_id == b.pack_id:
            score += 0.4
        if (a.pack_id < 0 and b.pack_id < 0 and a.sex != b.sex
                and (getattr(a, "was_exiled", False)
                     or getattr(b, "was_exiled", False)
                     or getattr(a, "former_pack_rank", 1) > 2
                     or getattr(b, "former_pack_rank", 1) > 2)):
            score += 1.8
        if a.sex == b.sex == "male" and a.is_adult and b.is_adult:
            score -= 0.15
        return score

    def _dominance_score(self, wolf) -> float:
        return (
            wolf.genetic_strength * 2.0
            + wolf.genetic_speed * 1.35
            + wolf.adult_size_scale * 1.45
            + (wolf.hp / max(1.0, wolf.max_hp)) * 1.1
            + getattr(wolf, "reproductive_success", 0) * 0.45
            + (getattr(wolf, "mates_count", 0) * 0.8 if wolf.sex == "male" else 0.0)
            + (0.5 if wolf.mate_bond_id is not None else 0.0)
            - (2.0 if wolf.is_beta else 0.0)
        )

    def _alpha_score(self, wolf) -> float:
        score = self._dominance_score(wolf)
        if wolf.is_adult:
            score += 1.0
        if wolf.sex == "male" and wolf.is_adult and not wolf.is_beta:
            score += 4.0
        elif wolf.sex == "male":
            score += 1.0
        return score

    def _apply_pack_politics(self, dt: float, wolves: list):
        by_pack: dict[int, list] = {}
        for wolf in wolves:
            if wolf.pack_id >= 0 and not getattr(wolf, "true_loner", False):
                by_pack.setdefault(wolf.pack_id, []).append(wolf)

        for pid, members in by_pack.items():
            adults = [w for w in members if w.is_adult]
            if len(adults) < 2:
                continue

            data = self._packs.get(pid)
            if data is None:
                continue
            adult_males = [w for w in adults if w.sex == "male"]
            adult_females = [w for w in adults if w.sex == "female"]
            male_frac = len(adult_males) / max(1, len(adults))
            female_frac = len(adult_females) / max(1, len(adults))

            data.ratio_fight_timer = max(0.0, data.ratio_fight_timer - dt)
            data.mate_challenge_timer = max(0.0, data.mate_challenge_timer - dt)

            if female_frac >= PACK_FEMALE_EXCESS_FRAC and len(adult_females) >= 3 and data.ratio_fight_timer <= 0.0:
                self._ratio_exile_fight(
                    aggressors=sorted(adult_females, key=self._dominance_score, reverse=True),
                    victim=min(adult_females, key=self._dominance_score),
                )
                data.ratio_fight_timer = PACK_RATIO_FIGHT_INTERVAL
            elif male_frac >= PACK_MALE_EXCESS_FRAC and len(adult_males) >= 2 and data.ratio_fight_timer <= 0.0:
                self._ratio_exile_fight(
                    aggressors=sorted(adult_males, key=self._dominance_score, reverse=True)[:-1],
                    victim=min(adult_males, key=self._dominance_score),
                )
                data.ratio_fight_timer = PACK_RATIO_FIGHT_INTERVAL
            else:
                unstable = len(adults) >= PACK_INSTABILITY_SIZE or male_frac > PACK_MALE_SOFT_CAP_FRAC
                if unstable and len(adult_males) >= 2:
                    chance = PACK_FIGHT_CHANCE * dt * (1.0 + max(0.0, male_frac - PACK_MALE_SOFT_CAP_FRAC) * 4.0)
                    if random.random() < chance:
                        contenders = [w for w in adult_males if not w.is_beta]
                        if len(contenders) >= 2:
                            contenders.sort(key=self._dominance_score, reverse=True)
                            a = contenders[0]
                            b = random.choice(contenders[1:])
                            self._resolve_male_conflict(a, b)

            if data.mate_challenge_timer <= 0.0:
                self._run_mate_challenges(adult_females, adult_males)
                data.mate_challenge_timer = PACK_CHALLENGE_INTERVAL

            if len(adults) >= PACK_INSTABILITY_SIZE:
                self._apply_split_pressure(adults)

    def _resolve_male_conflict(self, a, b):
        if self._dominance_score(a) >= self._dominance_score(b):
            winner, loser = a, b
        else:
            winner, loser = b, a

        roll = random.random()
        if roll < PACK_DEATH_CHANCE:
            loser.hp = 0.0
            loser._die()
        elif roll < PACK_DEATH_CHANCE + PACK_EXILE_CHANCE:
            loser.former_pack_rank = getattr(loser, "pack_rank", loser.former_pack_rank)
            loser.was_exiled = True
            loser.solo_excursion = False
            loser.pack_id = -1
            loser.mate_bond_id = None if loser.mate_bond_id == winner.wolf_id else loser.mate_bond_id
            angle = random.uniform(0, 2 * math.pi)
            loser.tx += math.cos(angle) * 8.0
            loser.ty += math.sin(angle) * 8.0
            loser.state = "walk"
        else:
            loser.beta_timer = random.uniform(900.0, 1200.0)
            loser.former_pack_rank = getattr(loser, "pack_rank", loser.former_pack_rank)

        winner.beta_timer = 0.0

    def _ratio_exile_fight(self, aggressors: list, victim):
        if victim is None or not victim.alive or victim.dead_state is not None:
            return
        active_aggressors = [
            w for w in aggressors
            if w is not victim and w.alive and w.dead_state is None
        ]
        if not active_aggressors:
            return
        total = sum(random.uniform(*PACK_RATIO_FIGHT_DAMAGE) * max(0.75, a.genetic_strength)
                    for a in active_aggressors[:4])
        victim.hp = max(0.0, victim.hp - total)
        if victim.hp <= victim.max_hp * 0.50:
            self._expel_wolf(victim, active_aggressors)

    def _run_mate_challenges(self, adult_females: list, adult_males: list):
        if len(adult_males) < 2:
            return
        males = [m for m in adult_males if m.alive and m.dead_state is None and not m.is_beta]
        if len(males) < 2:
            return
        for female in adult_females:
            preferred = getattr(female, "preferred_mate_id", None)
            if preferred is None:
                continue
            incumbent = next((m for m in males if m.wolf_id == preferred), None)
            if incumbent is None:
                female.preferred_mate_id = None
                continue
            challengers = [m for m in males if m is not incumbent and not female.related_to(m)]
            if not challengers:
                continue
            challenger = max(challengers, key=self._dominance_score)
            if self._dominance_score(challenger) <= self._dominance_score(incumbent) + 0.35:
                continue
            winner, loser = self._mate_challenge(incumbent, challenger)
            female.preferred_mate_id = winner.wolf_id
            female.mate_bond_id = winner.wolf_id
            winner.mate_bond_id = female.wolf_id
            if loser.hp <= loser.max_hp * 0.50:
                loser.beta_timer = max(loser.beta_timer, 600.0)

    def _mate_challenge(self, incumbent, challenger):
        a_hp = incumbent.hp
        b_hp = challenger.hp
        while a_hp > incumbent.max_hp * 0.50 and b_hp > challenger.max_hp * 0.50:
            b_hp -= random.uniform(*PACK_CHALLENGE_DAMAGE) * max(0.85, incumbent.genetic_strength)
            if b_hp <= challenger.max_hp * 0.50:
                break
            a_hp -= random.uniform(*PACK_CHALLENGE_DAMAGE) * max(0.85, challenger.genetic_strength)
        incumbent.hp = max(incumbent.max_hp * 0.50, a_hp)
        challenger.hp = max(challenger.max_hp * 0.50, b_hp)
        if incumbent.hp <= incumbent.max_hp * 0.50 and challenger.hp > challenger.max_hp * 0.50:
            return challenger, incumbent
        return incumbent, challenger

    def _expel_wolf(self, loser, aggressors: list):
        loser.former_pack_rank = getattr(loser, "pack_rank", loser.former_pack_rank)
        loser.was_exiled = True
        loser.solo_excursion = False
        loser.pack_id = -1
        loser.preferred_mate_id = None
        loser.exile_home_x = getattr(loser, "pack_home_x", loser.tx)
        loser.exile_home_y = getattr(loser, "pack_home_y", loser.ty)
        loser.exile_home_radius = PACK_EXILE_AVOID_HOME_RADIUS
        for aggressor in aggressors:
            loser.wary_of_wolf_ids.add(aggressor.wolf_id)
        if loser.mate_bond_id in {a.wolf_id for a in aggressors}:
            loser.mate_bond_id = None
        angle = random.uniform(0, 2 * math.pi)
        loser.tx += math.cos(angle) * 12.0
        loser.ty += math.sin(angle) * 12.0
        loser.state = "flee"
        loser._flee_timer = 45.0
        if aggressors:
            center_x = sum(a.tx for a in aggressors) / len(aggressors)
            center_y = sum(a.ty for a in aggressors) / len(aggressors)
            loser._flee_cx = center_x
            loser._flee_cy = center_y

    def _can_share_pack(self, a, b) -> bool:
        if getattr(a, "solo_excursion", False) or getattr(b, "solo_excursion", False):
            return False
        if (getattr(a, "wary_of_wolf_ids", None) and b.wolf_id in a.wary_of_wolf_ids):
            return False
        if (getattr(b, "wary_of_wolf_ids", None) and a.wolf_id in b.wary_of_wolf_ids):
            return False
        if getattr(a, "pair_bond_only", False) or getattr(b, "pair_bond_only", False):
            return (
                (
                    getattr(a, "mate_bond_id", None) == getattr(b, "wolf_id", None)
                    and getattr(b, "mate_bond_id", None) == getattr(a, "wolf_id", None)
                )
                or a.related_to(b)
            )
        return True

    def _update_lone_bonds(self, wolves: list):
        pair_sq = PACK_COUPLE_FORM_RADIUS ** 2
        lone = [w for w in wolves if w.pack_id < 0 and w.is_adult and not w.true_loner]

        for wolf in lone:
            mate = next(
                (other for other in lone
                 if other is not wolf and other.wolf_id == wolf.mate_bond_id),
                None,
            )
            if mate is not None:
                wolf.pair_bond_only = True
                mate.pair_bond_only = True

        for wolf in lone:
            if wolf.sex != "male" or wolf.pair_bond_only:
                continue
            best = None
            best_score = -1e9
            for other in lone:
                if (other is wolf or other.sex != "female" or other.pair_bond_only
                        or other.pregnant or wolf.related_to(other)):
                    continue
                ddx = other.tx - wolf.tx
                ddy = other.ty - wolf.ty
                dist_sq = ddx * ddx + ddy * ddy
                if dist_sq > pair_sq:
                    continue
                score = self._social_affinity(wolf, other) - math.sqrt(dist_sq) * 0.08
                if wolf.mate_bond_id == other.wolf_id:
                    score += 5.0
                if score > best_score:
                    best = other
                    best_score = score
            if best is None or best_score < 1.1:
                continue
            if random.random() > PACK_COUPLE_BOND_CHANCE:
                continue
            wolf.mate_bond_id = best.wolf_id
            best.mate_bond_id = wolf.wolf_id
            wolf.pair_bond_only = True
            best.pair_bond_only = True
            wolf.true_loner = False
            best.true_loner = False

    def _territory_radius_for_pack(self, members: list) -> float:
        return max(
            PACK_TERRITORY_MIN_RADIUS,
            min(PACK_TERRITORY_MAX_RADIUS,
                PACK_TERRITORY_BASE_RADIUS + len(members) * PACK_TERRITORY_PER_WOLF)
        )

    def _home_radius_for_pack(self, members: list) -> float:
        return PACK_HOME_BASE_RADIUS + len(members) * PACK_HOME_PER_WOLF

    def _territory_cell_for_point(self, x: float, y: float) -> tuple[int, int]:
        cell = PACK_TERRITORY_CELL_SIZE
        return int(math.floor(x / cell)), int(math.floor(y / cell))

    def _territory_point_for_cell(self, cell_xy: tuple[int, int]) -> tuple[float, float]:
        cell = PACK_TERRITORY_CELL_SIZE
        return ((cell_xy[0] + 0.5) * cell, (cell_xy[1] + 0.5) * cell)

    def _territory_neighbors(self, cell_xy: tuple[int, int]):
        cx, cy = cell_xy
        for ox, oy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            yield (cx + ox, cy + oy)

    def _territory_cell_walkable(self, grid: list | None, cell_xy: tuple[int, int]) -> bool:
        if not grid:
            return True
        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        px, py = self._territory_point_for_cell(cell_xy)
        sx = int(px)
        sy = int(py)
        return 0 <= sy < rows and 0 <= sx < cols and is_walkable_tile(grid, sy, sx)

    def _territory_cell_distance(self, a: tuple[int, int], b: tuple[int, int]) -> int:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def _ensure_home_cell(self, data: _PackData, grid: list | None):
        home_cell = self._territory_cell_for_point(data.home_x, data.home_y)
        data.home_cell = home_cell
        if home_cell not in data.territory_cells and self._territory_cell_walkable(grid, home_cell):
            data.territory_cells.add(home_cell)
            data.territory_heat[home_cell] = max(data.territory_heat.get(home_cell, 0.0),
                                                 PACK_TERRITORY_HEAT_HOME)

    def _grow_territory_path(self, data: _PackData, start_xy: tuple[float, float],
                             end_xy: tuple[float, float], grid: list | None,
                             heat_add: float, max_new_cells: int = PACK_TERRITORY_GROW_LIMIT):
        start_cell = self._territory_cell_for_point(*start_xy)
        end_cell = self._territory_cell_for_point(*end_xy)
        if not data.territory_cells:
            data.territory_cells.add(start_cell)
        current = start_cell if start_cell in data.territory_cells else (
            min(data.territory_cells, key=lambda c: self._territory_cell_distance(c, start_cell))
        )
        added = 0
        visited = set()
        while current != end_cell and added <= max_new_cells + 2:
            visited.add(current)
            cx, cy = current
            dx = end_cell[0] - cx
            dy = end_cell[1] - cy
            step = current
            if abs(dx) >= abs(dy) and dx != 0:
                step = (cx + (1 if dx > 0 else -1), cy)
            elif dy != 0:
                step = (cx, cy + (1 if dy > 0 else -1))
            if step in visited:
                break
            if not self._territory_cell_walkable(grid, step):
                break
            if step not in data.territory_cells:
                if added >= max_new_cells:
                    break
                data.territory_cells.add(step)
                added += 1
            data.territory_heat[step] = data.territory_heat.get(step, 0.0) + heat_add
            current = step
        data.territory_heat[current] = data.territory_heat.get(current, 0.0) + heat_add

    def _territory_centroid_and_extent(self, cells: set[tuple[int, int]], home_cell: tuple[int, int]) -> tuple[float, float, float]:
        if not cells:
            hx, hy = self._territory_point_for_cell(home_cell)
            return hx, hy, PACK_TERRITORY_MIN_RADIUS
        pts = [self._territory_point_for_cell(cell) for cell in cells]
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        extent = max(math.hypot(px - cx, py - cy) for px, py in pts)
        return cx, cy, max(PACK_TERRITORY_MIN_RADIUS * 0.35, extent + PACK_TERRITORY_CELL_SIZE * 0.75)

    def _prune_territory(self, data: _PackData):
        if len(data.territory_cells) <= 1 or data.home_cell not in data.territory_cells:
            return
        candidates = sorted(
            [cell for cell in data.territory_cells if cell != data.home_cell],
            key=lambda cell: (
                data.territory_heat.get(cell, 0.0),
                -self._territory_cell_distance(cell, data.home_cell)
            )
        )
        for cell in candidates:
            if data.territory_heat.get(cell, 0.0) > PACK_TERRITORY_PRUNE_HEAT:
                break
            neighbors = [n for n in self._territory_neighbors(cell) if n in data.territory_cells]
            if len(neighbors) > 1:
                continue
            data.territory_cells.discard(cell)
            data.territory_heat.pop(cell, None)

    def _territory_contains_point(self, data: _PackData, x: float, y: float) -> bool:
        return self._territory_cell_for_point(x, y) in data.territory_cells

    def _land_quality(self, grid: list | None, cx: float, cy: float, radius: float) -> float:
        if not grid:
            return 1.0
        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        if rows == 0 or cols == 0:
            return 1.0
        sample_points = 0
        walkable = 0
        step = max(8.0, radius / 3.5)
        for ox in (-radius, -step, 0.0, step, radius):
            for oy in (-radius, -step, 0.0, step, radius):
                sx = int(cx + ox)
                sy = int(cy + oy)
                if 0 <= sy < rows and 0 <= sx < cols:
                    sample_points += 1
                    if is_walkable_tile(grid, sy, sx):
                        walkable += 1
        if sample_points == 0:
            return 0.0
        return walkable / sample_points

    def _resource_score(self, sheep_list: list, cx: float, cy: float, radius: float,
                        territory_cells: set[tuple[int, int]] | None = None) -> float:
        radius_sq = radius * radius
        score = 0.0
        for sheep in sheep_list:
            if sheep.dead_state is not None or not sheep.alive:
                continue
            if territory_cells is not None:
                if self._territory_cell_for_point(sheep.tx, sheep.ty) not in territory_cells:
                    continue
            ddx = sheep.tx - cx
            ddy = sheep.ty - cy
            dist_sq = ddx * ddx + ddy * ddy
            if territory_cells is None and dist_sq > radius_sq:
                continue
            score += 1.0
            if hasattr(sheep, "maturity_age") and sheep.age < sheep.maturity_age:
                score += 0.8
            max_hp = float(getattr(sheep, "genetic_hp", 1))
            if max_hp > 0 and sheep.hp / max_hp < 0.6:
                score += 0.6
        return score

    def _territory_score(self, sheep_list: list, grid: list | None,
                         cx: float, cy: float, radius: float) -> tuple[float, float]:
        land = self._land_quality(grid, cx, cy, radius)
        resources = self._resource_score(sheep_list, cx, cy, radius)
        return resources * (0.55 + land * 0.9), resources

    def _find_best_territory_anchor(self, data: _PackData, members: list,
                                    sheep_list: list, grid: list | None) -> tuple[float, float, float, float]:
        target_radius = self._territory_radius_for_pack(members)
        candidates = [
            (data.territory_cx or data.cx, data.territory_cy or data.cy),
            (data.cx, data.cy),
            (data.home_x or data.cx, data.home_y or data.cy),
        ]
        for sheep in sheep_list:
            if sheep.dead_state is not None or not sheep.alive:
                continue
            if math.hypot(sheep.tx - data.cx, sheep.ty - data.cy) <= target_radius + PACK_TERRITORY_SEARCH_STEP:
                candidates.append((sheep.tx, sheep.ty))

        best_cx = data.territory_cx or data.cx
        best_cy = data.territory_cy or data.cy
        best_quality, best_resources = self._territory_score(
            sheep_list, grid, best_cx, best_cy, target_radius
        )
        for cand_x, cand_y in candidates[:24]:
            quality, resources = self._territory_score(sheep_list, grid, cand_x, cand_y, target_radius)
            drift_penalty = math.hypot(cand_x - data.cx, cand_y - data.cy) * 0.01
            score = quality - drift_penalty
            if score > best_quality:
                best_quality = score
                best_resources = resources
                best_cx = cand_x
                best_cy = cand_y
        return best_cx, best_cy, target_radius, best_resources

    def _update_pack_territory(self, dt: float, data: _PackData, members: list,
                               sheep_list: list, grid: list | None):
        data.territory_timer = max(0.0, data.territory_timer - dt)
        data.territory_conflict_timer = max(0.0, data.territory_conflict_timer - dt)
        desired_home = self._home_radius_for_pack(members)
        if data.home_x == 0.0 and data.home_y == 0.0:
            data.home_x = data.cx
            data.home_y = data.cy
        self._ensure_home_cell(data, grid)

        # Territory gradually cools unless actively used.
        for cell_xy in list(data.territory_heat.keys()):
            data.territory_heat[cell_xy] = max(0.0, data.territory_heat[cell_xy] - PACK_TERRITORY_HEAT_DECAY * dt)
            if data.territory_heat[cell_xy] <= 0.0 and cell_xy != data.home_cell:
                data.territory_heat.pop(cell_xy, None)

        data.territory_heat[data.home_cell] = max(
            PACK_TERRITORY_HEAT_HOME,
            data.territory_heat.get(data.home_cell, 0.0) + PACK_TERRITORY_HEAT_HOME * dt * 0.03
        )

        # Members reinforce the area they actually use and extend the claim from home.
        for wolf in members:
            wolf_cell = self._territory_cell_for_point(wolf.tx, wolf.ty)
            if wolf_cell in data.territory_cells:
                data.territory_heat[wolf_cell] = data.territory_heat.get(wolf_cell, 0.0) + PACK_TERRITORY_HEAT_MEMBER
            else:
                self._grow_territory_path(
                    data,
                    (data.home_x, data.home_y),
                    (wolf.tx, wolf.ty),
                    grid,
                    PACK_TERRITORY_HEAT_MEMBER,
                )
            if data.mode == _PackData.HUNT and wolf._hunt_target is not None:
                self._grow_territory_path(
                    data,
                    (wolf.tx, wolf.ty),
                    (wolf._hunt_target.tx, wolf._hunt_target.ty),
                    grid,
                    PACK_TERRITORY_HEAT_HUNT,
                    max_new_cells=PACK_TERRITORY_GROW_LIMIT + 2,
                )

        # If the current territory is poor, push toward nearby prey even if it is outside.
        target_cells = max(
            10,
            int((self._territory_radius_for_pack(members) / PACK_TERRITORY_CELL_SIZE) ** 2 * 1.35)
        )
        if data.hunt_target is not None and not self._territory_contains_point(data, data.hunt_target.tx, data.hunt_target.ty):
            self._grow_territory_path(
                data,
                (data.home_x, data.home_y),
                (data.hunt_target.tx, data.hunt_target.ty),
                grid,
                PACK_TERRITORY_HEAT_HUNT,
                max_new_cells=PACK_TERRITORY_GROW_LIMIT + 3,
            )
        elif len(data.territory_cells) < target_cells:
            # Expand toward the best nearby prey cluster when the pack still has room to grow.
            outside_prey = [
                s for s in sheep_list
                if s.alive and s.dead_state is None and not self._territory_contains_point(data, s.tx, s.ty)
            ]
            if outside_prey:
                best_prey = min(outside_prey, key=lambda s: math.hypot(s.tx - data.home_x, s.ty - data.home_y))
                self._grow_territory_path(
                    data,
                    (data.home_x, data.home_y),
                    (best_prey.tx, best_prey.ty),
                    grid,
                    PACK_TERRITORY_HEAT_MEMBER,
                )

        self._prune_territory(data)
        data.territory_cx, data.territory_cy, data.territory_radius = self._territory_centroid_and_extent(
            data.territory_cells, data.home_cell
        )
        data.resource_score = self._resource_score(
            sheep_list, data.territory_cx, data.territory_cy, data.territory_radius,
            territory_cells=data.territory_cells
        )
        data.territory_quality = self._land_quality(
            grid, data.territory_cx, data.territory_cy, data.territory_radius
        )
        if data.territory_timer <= 0.0:
            data.territory_timer = random.uniform(PACK_TERRITORY_REEVAL_MIN, PACK_TERRITORY_REEVAL_MAX)

        data.home_radius = desired_home
        if data.mode == _PackData.CAMP:
            data.home_x = data.camp_x
            data.home_y = data.camp_y
        else:
            pull = min(1.0, dt * 0.08)
            data.home_x += (data.territory_cx - data.home_x) * pull
            data.home_y += (data.territory_cy - data.home_y) * pull
        data.home_cell = self._territory_cell_for_point(data.home_x, data.home_y)
        data.territory_cells.add(data.home_cell)
        data.territory_heat[data.home_cell] = max(data.territory_heat.get(data.home_cell, 0.0),
                                                  PACK_TERRITORY_HEAT_HOME)

    def _pack_power(self, members: list) -> float:
        return sum(max(0.5, self._dominance_score(w)) for w in members if w.alive and w.dead_state is None)

    def _resolve_territory_conflicts(self, dt: float, by_pack: dict[int, list]):
        pack_ids = list(by_pack.keys())
        for i, pid_a in enumerate(pack_ids):
            data_a = self._packs.get(pid_a)
            if data_a is None:
                continue
            for pid_b in pack_ids[i + 1:]:
                data_b = self._packs.get(pid_b)
                if data_b is None:
                    continue
                overlap_cells = data_a.territory_cells & data_b.territory_cells
                if len(overlap_cells) <= 1:
                    continue
                if data_a.territory_conflict_timer > 0.0 or data_b.territory_conflict_timer > 0.0:
                    continue
                members_a = by_pack[pid_a]
                members_b = by_pack[pid_b]
                power_a = self._pack_power(members_a)
                power_b = self._pack_power(members_b)
                damage_a = random.uniform(*PACK_TERRITORY_CONFLICT_DAMAGE) * max(1.0, power_b / max(1.0, len(members_b)))
                damage_b = random.uniform(*PACK_TERRITORY_CONFLICT_DAMAGE) * max(1.0, power_a / max(1.0, len(members_a)))
                weakest_a = min(members_a, key=lambda w: w.hp / max(1.0, w.max_hp))
                weakest_b = min(members_b, key=lambda w: w.hp / max(1.0, w.max_hp))
                weakest_a.hp = max(0.0, weakest_a.hp - damage_a)
                weakest_b.hp = max(0.0, weakest_b.hp - damage_b)
                if weakest_a.hp <= 0.0:
                    weakest_a._die()
                if weakest_b.hp <= 0.0:
                    weakest_b._die()
                if power_a >= power_b:
                    winner_data, loser_data = data_a, data_b
                    winner_members, loser_members = members_a, members_b
                else:
                    winner_data, loser_data = data_b, data_a
                    winner_members, loser_members = members_b, members_a
                cut_cells = sorted(
                    overlap_cells,
                    key=lambda cell: (
                        loser_data.territory_heat.get(cell, 0.0),
                        -self._territory_cell_distance(cell, loser_data.home_cell)
                    )
                )
                cut_n = max(1, len(cut_cells) // 2)
                for cell_xy in cut_cells[:cut_n]:
                    if cell_xy == loser_data.home_cell:
                        continue
                    loser_data.territory_cells.discard(cell_xy)
                    loser_data.territory_heat.pop(cell_xy, None)
                    winner_data.territory_cells.add(cell_xy)
                    winner_data.territory_heat[cell_xy] = max(
                        winner_data.territory_heat.get(cell_xy, 0.0),
                        PACK_TERRITORY_HEAT_HUNT
                    )
                self._prune_territory(loser_data)
                loser_data.territory_cx, loser_data.territory_cy, loser_data.territory_radius = (
                    self._territory_centroid_and_extent(loser_data.territory_cells, loser_data.home_cell)
                )
                winner_data.territory_cx, winner_data.territory_cy, winner_data.territory_radius = (
                    self._territory_centroid_and_extent(winner_data.territory_cells, winner_data.home_cell)
                )
                for wolf in loser_members:
                    wolf._flee_cx = winner_data.territory_cx
                    wolf._flee_cy = winner_data.territory_cy
                    if wolf.hp < wolf.max_hp * 0.65:
                        wolf.state = "flee"
                        wolf._flee_timer = max(getattr(wolf, "_flee_timer", 0.0), 18.0)
                data_a.territory_conflict_timer = PACK_TERRITORY_CONFLICT_INTERVAL
                data_b.territory_conflict_timer = PACK_TERRITORY_CONFLICT_INTERVAL

    def _update_pack_travel(self, dt: float, data: _PackData, members: list):
        data.travel_timer = max(0.0, data.travel_timer - dt)
        if data.mode == _PackData.HUNT and data.hunt_target is not None:
            data.travel_x = data.hunt_target.tx
            data.travel_y = data.hunt_target.ty
            data.travel_timer = PACK_TARGET_SCAN_INTERVAL
            return
        if data.mode == _PackData.CAMP:
            data.travel_x = data.home_x
            data.travel_y = data.home_y
            data.travel_timer = data.camp_timer
            return

        dist_to_goal = math.hypot(data.travel_x - data.cx, data.travel_y - data.cy)
        if data.travel_timer > 0.0 and dist_to_goal > 10.0:
            return

        if data.territory_cells:
            if data.resting:
                candidate_cells = [
                    cell for cell in data.territory_cells
                    if self._territory_cell_distance(cell, data.home_cell) <= 2
                ] or [data.home_cell]
            else:
                candidate_cells = list(data.territory_cells)
            chosen = random.choice(candidate_cells)
            data.travel_x, data.travel_y = self._territory_point_for_cell(chosen)
        else:
            data.travel_x = data.home_x
            data.travel_y = data.home_y
        data.travel_timer = random.uniform(PACK_TRAVEL_STEP_MIN, PACK_TRAVEL_STEP_MAX)

    def _apply_split_pressure(self, adults: list):
        anchors = sorted(adults, key=self._dominance_score, reverse=True)
        if len(anchors) < 2:
            return
        leader_a = anchors[0]
        leader_b = max(
            anchors[1:],
            key=lambda w: (self._social_affinity(w, leader_a) * -1.0) + leader_a.genetic_similarity(w) * -0.5
        )
        center_x = sum(w.tx for w in adults) / len(adults)
        center_y = sum(w.ty for w in adults) / len(adults)

        for wolf in adults:
            score_a = self._social_affinity(wolf, leader_a)
            score_b = self._social_affinity(wolf, leader_b)
            if score_b > score_a:
                dx = wolf.tx - center_x
                dy = wolf.ty - center_y
                mag = math.sqrt(dx * dx + dy * dy) or 1.0
                wolf.tx += (dx / mag) * PACK_SPLIT_PUSH * 0.5
                wolf.ty += (dy / mag) * PACK_SPLIT_PUSH * 0.5

    # ------------------------------------------------------------------
    # Pack-level prey selection
    # ------------------------------------------------------------------

    def _find_pack_prey(self, cx: float, cy: float,
                        sheep_list: list, scan_r_sq: float,
                        territory_cells: set[tuple[int, int]] | None = None):
        """Best live prey target for the pack via hearing / scent.

        Shared pack targets stay on living sheep so the pack keeps pressuring
        the flock after the first kill. Individual wolves can still peel off to
        nearby fresh corpses using their own corpse-smell logic.
        """
        scent_sq  = 150.0 ** 2   # stationary live prey

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
            if territory_cells is not None and self._territory_cell_for_point(sheep.tx, sheep.ty) not in territory_cells:
                continue
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
