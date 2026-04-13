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

# Wolf threat alarm pushed to herd manager (tile radius around pack center)
WOLF_ALARM_RADIUS   = 45.0

# Pack awareness radius (written to each wolf as pack_awareness_radius)
PACK_AWARENESS_BASE     = 26.0
PACK_AWARENESS_PER_WOLF = 2.0
PACK_AWARENESS_MAX      = 52.0

PACK_MALE_SOFT_CAP_FRAC   = 0.30
PACK_INSTABILITY_SIZE     = MAX_PACK_SIZE
PACK_FIGHT_CHANCE         = 0.007  # real wolves rarely fight to the death within the pack
PACK_SPLIT_PUSH           = 2.2
PACK_BETA_CHANCE          = 0.40   # submission is the most common conflict outcome
PACK_EXILE_CHANCE         = 0.56   # exile is common for persistent challengers
PACK_DEATH_CHANCE         = 0.04   # intra-pack killing is rare — wolves know their packmates


# ---------------------------------------------------------------------------
# Per-pack state
# ---------------------------------------------------------------------------

class _PackData:
    __slots__ = ("cx", "cy", "size", "hunt_target", "scan_timer",
                 "awareness_radius", "mode", "chill_timer", "alpha_id",
                 "feast_timer", "camp_timer", "camp_x", "camp_y",
                 "meal_corpse_id", "blocked_corpse_id")

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

        if living:
            self._apply_pack_politics(dt, living)

        self._timer -= dt
        if self._timer <= 0:
            self._timer = REASSIGN_INTERVAL
            if living:
                self._reassign(living)
                self._reassemble_packs(living)
                self._split_oversized_packs(living)

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
            if getattr(w, "true_loner", False):
                continue
            key = (int(w.tx / cell), int(w.ty / cell))
            spatial.setdefault(key, []).append(w)

        old_ids = {id(w): w.pack_id for w in wolves}

        for w in wolves:
            w.pack_id = -1

        pack_id = 0
        for seed in wolves:
            if getattr(seed, "true_loner", False):
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

    def _update_packs(self, dt: float, wolves: list, sheep_list: list):
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
                w.pack_camp_x           = w.tx
                w.pack_camp_y           = w.ty
                w.pack_blocked_corpse_id = None

        scan_r_sq = PACK_TARGET_RADIUS ** 2

        for pid, members in by_pack.items():
            data = self._packs.get(pid)
            if data is None:
                data = _PackData()
                self._packs[pid] = data

            n  = len(members)
            alpha = max(members, key=self._alpha_score)
            data.alpha_id         = alpha.wolf_id
            data.cx               = alpha.tx
            data.cy               = alpha.ty
            data.size             = n
            data.awareness_radius = min(PACK_AWARENESS_MAX,
                                        PACK_AWARENESS_BASE + (n - 1) * PACK_AWARENESS_PER_WOLF)

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
                data.mode = _PackData.HUNT if (any_hungry or any_desperate or avg_hunger >= 0.42) else _PackData.CHILL
                if data.mode == _PackData.CHILL:
                    data.hunt_target = None
                    data.feast_timer = 0.0
                    data.camp_timer = 0.0
                    data.chill_timer = max(data.chill_timer, random.uniform(PACK_CHILL_MIN * 0.35,
                                                                            PACK_CHILL_MAX * 0.55))
                else:
                    data.blocked_corpse_id = None

            # Periodic pack-level prey scan
            data.scan_timer -= dt
            if data.mode == _PackData.HUNT and data.scan_timer <= 0:
                data.scan_timer = PACK_TARGET_SCAN_INTERVAL
                best = self._find_pack_prey(data.cx, data.cy, sheep_list, scan_r_sq)
                data.hunt_target = best

            # Push attributes onto all pack members
            for w in members:
                w.pack_cx               = data.cx
                w.pack_cy               = data.cy
                w.pack_size             = data.size
                w.pack_hunt_target      = data.hunt_target
                w.pack_awareness_radius = data.awareness_radius
                w.pack_mode             = data.mode
                w.pack_mode_timer       = data.camp_timer if data.mode == _PackData.CAMP else data.chill_timer
                w.pack_alpha_id         = data.alpha_id
                w.pack_is_alpha         = (w.wolf_id == data.alpha_id)
                w.pack_camp_x           = data.camp_x if data.mode == _PackData.CAMP else data.cx
                w.pack_camp_y           = data.camp_y if data.mode == _PackData.CAMP else data.cy
                w.pack_blocked_corpse_id = data.blocked_corpse_id

    def _social_affinity(self, a, b) -> float:
        score = 0.0
        if getattr(a, "mate_bond_id", None) == getattr(b, "wolf_id", None):
            score += 4.0
        if a.related_to(b):
            score += 2.5
        score += a.genetic_similarity(b) * 1.5
        if a.pack_id >= 0 and a.pack_id == b.pack_id:
            score += 0.4
        if a.sex == b.sex == "male" and a.is_adult and b.is_adult:
            score -= 0.15
        return score

    def _dominance_score(self, wolf) -> float:
        return (
            wolf.genetic_strength * 1.8
            + wolf.adult_size_scale * 1.2
            + wolf.hp / max(1.0, wolf.max_hp)
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

        for members in by_pack.values():
            adults = [w for w in members if w.is_adult]
            if len(adults) < 2:
                continue

            adult_males = [w for w in adults if w.sex == "male"]
            adult_females = [w for w in adults if w.sex == "female"]
            male_frac = len(adult_males) / max(1, len(adults))
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
            loser.pack_id = -1
            loser.mate_bond_id = None if loser.mate_bond_id == winner.wolf_id else loser.mate_bond_id
            angle = random.uniform(0, 2 * math.pi)
            loser.tx += math.cos(angle) * 8.0
            loser.ty += math.sin(angle) * 8.0
            loser.state = "walk"
        else:
            loser.beta_timer = random.uniform(900.0, 1200.0)

        winner.beta_timer = 0.0

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
                        sheep_list: list, scan_r_sq: float):
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
