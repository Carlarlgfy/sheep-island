"""
WolfPackManager — groups nearby wolves into packs, coordinates the REST/HUNT
cycle, maintains grass/dirt home bases, and enforces social hierarchy.
Modelled on HerdManager's structure but for predators.
"""

import math
import random

from wolf import WOLF_HUNGER_HUNT
from mapgen import is_walkable_tile, GRASS, DIRT, SNOW

DAY = 300.0   # sim-seconds per in-game day

# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------

PACK_PROXIMITY    = 42.0   # tiles — wolves this close form one pack (wider → helps consolidation)
REASSIGN_INTERVAL = 4.0    # seconds between proximity re-groupings
OPTIMAL_PACK_SIZE = 10
MAX_PACK_SIZE     = 20

# Home base
HOME_GOOD_TILES      = {GRASS, DIRT}
HOME_BAD_TILES       = {"sand", SNOW, "water", "wall", "tundra"}
HOME_RADIUS_BASE     = 16.0   # base territory radius in tiles
HOME_RADIUS_PER_WOLF = 2.4    # each additional wolf expands territory further
HOME_MIN_SEPARATION  = 60.0   # min tiles between any two pack homes
HOME_RIVAL_TOO_CLOSE = 30.0   # rival pack centre this close → relocate home
HOME_RECHECK_MIN     = 200.0  # seconds between home-suitability checks
HOME_RECHECK_MAX     = 400.0

# Pack mode cycle
HUNT_TRIGGER_AVG  = 0.30   # pack switches to HUNT when avg hunger > this
REST_HUNGER_MAX   = 0.20   # pack fully rests when avg hunger < this

# Shared prey scan
SCAN_INTERVAL = 6.0
SCAN_RADIUS   = 120.0

# Pack travel step when patrolling in HUNT with no target
TRAVEL_STEP_MIN = 60.0
TRAVEL_STEP_MAX = 140.0

# Social hierarchy / exile
# Target ratio: ~30% male, ~70% female
# Exile triggers: >43% male → chase away weakest male; >85% female → chase away weakest female
RATIO_FIGHT_INTERVAL = 45.0    # seconds between ratio-correction fights
RATIO_FIGHT_DAMAGE   = (3.0, 7.0)
FEMALE_EXCESS_FRAC   = 0.85    # exile weakest female when female fraction >= this
MALE_EXCESS_FRAC     = 0.43    # exile weakest male when male fraction >= this
EXILE_HP_THRESHOLD   = 0.45    # victim expelled when HP drops below this fraction of max

# Rival pack / lone wolf pressure
RIVAL_ENCOUNTER_R = 45.0   # range at which packs notice each other
LONE_FLEE_R       = 30.0   # lone wolves flee pack centres within this range

# Large pack pair exodus
EXODUS_MIN_SIZE = 10       # adults needed before voluntary departures start
EXODUS_CHANCE   = 0.00035  # probability per adult excess per second

# Home relocation when prey is scarce
HOME_RELOCATE_HUNT_TIME = DAY * 1.5   # after this long in HUNT with no kill, seek new home
HOME_RELOCATE_SEARCH_R  = 200.0       # tile radius to scan for prey-rich area


# ---------------------------------------------------------------------------
# Per-pack state
# ---------------------------------------------------------------------------

class _PackData:
    __slots__ = (
        "cx", "cy", "size", "mode", "scan_timer",
        "hunt_target", "travel_x", "travel_y", "travel_timer",
        "home_x", "home_y", "home_radius", "home_recheck_timer",
        "ratio_fight_timer", "alpha_id", "starve_timer",
        "name", "color_idx",
    )
    REST = "rest"
    HUNT = "hunt"

    def __init__(self):
        self.cx         = 0.0
        self.cy         = 0.0
        self.size       = 0
        self.mode       = _PackData.REST
        self.scan_timer = random.uniform(0, SCAN_INTERVAL)
        self.hunt_target = None
        self.travel_x   = 0.0
        self.travel_y   = 0.0
        self.travel_timer = 0.0
        self.home_x     = 0.0
        self.home_y     = 0.0
        self.home_radius = HOME_RADIUS_BASE
        self.home_recheck_timer = random.uniform(60.0, 180.0)
        self.ratio_fight_timer  = random.uniform(
            RATIO_FIGHT_INTERVAL * 0.3, RATIO_FIGHT_INTERVAL)
        self.alpha_id   = None
        self.starve_timer = 0.0
        self.name       = ""
        self.color_idx  = 0


# ---------------------------------------------------------------------------
# WolfPackManager
# ---------------------------------------------------------------------------

class WolfPackManager:

    # Pack name vocabulary — celestial bodies × wolf features
    _CELESTIAL = [
        "Sun", "Moon", "Earth", "Star", "Cloud", "Storm", "Frost",
        "Ash", "Tide", "Dusk", "Dawn", "Mist", "Ember", "Gale", "Void",
    ]
    _WOLF_PART = [
        "Fang", "Eye", "Tail", "Heart", "Paw",
        "Claw", "Mane", "Jaw", "Spine", "Ear",
    ]
    _name_counter = 0   # class-level so names are unique across all manager instances

    @classmethod
    def _new_pack_identity(cls) -> tuple[str, int]:
        idx       = cls._name_counter
        cls._name_counter += 1
        celestial = cls._CELESTIAL[(idx // len(cls._WOLF_PART)) % len(cls._CELESTIAL)]
        part      = cls._WOLF_PART[idx % len(cls._WOLF_PART)]
        color_idx = idx % 8
        return f"{celestial} {part}", color_idx

    def __init__(self):
        self._timer = 0.0
        self._packs: dict[int, _PackData] = {}
        self._grid: list | None = None

    # ------------------------------------------------------------------
    # Public update — called once per frame from main.py
    # ------------------------------------------------------------------

    def update(self, dt: float, wolves: list, sheep_list: list,
               grid: list | None = None):
        self._grid = grid
        living = [w for w in wolves if w.alive and w.dead_state is None]

        if living:
            self._apply_pack_politics(dt, living)
            self._apply_lone_wolf_pressure(living)

        self._timer -= dt
        if self._timer <= 0:
            self._timer = REASSIGN_INTERVAL
            if living:
                self._reassign(living)

        if living:
            by_pack: dict[int, list] = {}
            for w in living:
                if w.pack_id >= 0:
                    by_pack.setdefault(w.pack_id, []).append(w)
            self._update_packs(dt, living, by_pack, sheep_list, grid)
            self._apply_rival_pack_pressure(by_pack)

    # ------------------------------------------------------------------
    # Proximity grouping
    # ------------------------------------------------------------------

    def _reassign(self, wolves: list):
        cell = PACK_PROXIMITY
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

        # Build new pack dict and carry state forward from best-matching old pack
        new_packs: dict[int, _PackData] = {}
        for w in wolves:
            if w.pack_id >= 0 and w.pack_id not in new_packs:
                pd = _PackData()
                pd.name, pd.color_idx = self._new_pack_identity()
                new_packs[w.pack_id] = pd

        old_votes: dict[int, list] = {}
        for w in wolves:
            old = old_ids.get(id(w), -1)
            if old >= 0 and w.pack_id >= 0:
                old_votes.setdefault(old, []).append(w.pack_id)

        transferred: set = set()
        for old_id, votes in old_votes.items():
            if old_id not in self._packs:
                continue
            best_new = max(set(votes), key=votes.count)
            if best_new in new_packs and best_new not in transferred:
                od = self._packs[old_id]
                nd = new_packs[best_new]
                nd.mode              = od.mode
                nd.hunt_target       = od.hunt_target
                nd.scan_timer        = od.scan_timer
                nd.home_x            = od.home_x
                nd.home_y            = od.home_y
                nd.home_radius       = od.home_radius
                nd.home_recheck_timer = od.home_recheck_timer
                nd.ratio_fight_timer = od.ratio_fight_timer
                nd.alpha_id          = od.alpha_id
                nd.travel_x          = od.travel_x
                nd.travel_y          = od.travel_y
                nd.travel_timer      = od.travel_timer
                nd.name              = od.name       # pack keeps its name through regroupings
                nd.color_idx         = od.color_idx
                transferred.add(best_new)

        self._packs = new_packs

    # ------------------------------------------------------------------
    # Per-frame pack update
    # ------------------------------------------------------------------

    def _update_packs(self, dt: float, wolves: list, by_pack: dict,
                      sheep_list: list, grid: list | None):
        # Defaults for lone wolves
        for w in wolves:
            if w.pack_id < 0:
                w.pack_cx      = w.tx
                w.pack_cy      = w.ty
                w.pack_home_x  = w.tx
                w.pack_home_y  = w.ty
                w.pack_home_r  = HOME_RADIUS_BASE
                w.pack_mode    = "hunt" if w.hunger >= WOLF_HUNGER_HUNT else "rest"
                w.pack_target  = None
                w.pack_size    = 1
                w.pack_is_alpha = True
                w.pack_rank    = 1

        scan_sq = SCAN_RADIUS ** 2

        for pid, members in by_pack.items():
            data = self._packs.get(pid)
            if data is None:
                data = _PackData()
                self._packs[pid] = data

            n         = len(members)
            avg_x     = sum(w.tx for w in members) / n
            avg_y     = sum(w.ty for w in members) / n
            avg_hunger = sum(w.hunger for w in members) / n
            data.cx   = avg_x
            data.cy   = avg_y
            data.size  = n

            # Initialise home on first encounter
            if data.home_x == 0.0 and data.home_y == 0.0:
                hx, hy = self._find_grass_home(avg_x, avg_y)
                data.home_x, data.home_y = hx, hy
            data.home_radius = HOME_RADIUS_BASE + n * HOME_RADIUS_PER_WOLF

            # Periodic home relocation check
            data.home_recheck_timer = max(0.0, data.home_recheck_timer - dt)
            if data.home_recheck_timer <= 0.0:
                data.home_recheck_timer = random.uniform(HOME_RECHECK_MIN, HOME_RECHECK_MAX)
                if self._home_needs_move(data, grid):
                    hx, hy = self._find_better_home(data, grid)
                    data.home_x, data.home_y = hx, hy

            # Validate shared hunt target
            ht = data.hunt_target
            if ht is not None and (not ht.alive or ht.dead_state is not None):
                data.hunt_target = None

            # Pack mode — hunger drives the REST ↔ HUNT switch
            if avg_hunger >= HUNT_TRIGGER_AVG or any(w.hunger >= 0.65 for w in members):
                data.mode = _PackData.HUNT
            elif avg_hunger <= REST_HUNGER_MAX:
                data.mode = _PackData.REST

            # Starve timer: counts up while in HUNT with no prey in range
            if data.mode == _PackData.HUNT and data.hunt_target is None:
                data.starve_timer += dt
            else:
                data.starve_timer = 0.0

            # If the pack has been hunting fruitlessly for too long, move the home
            # toward the nearest sheep cluster so the territory shifts into new land
            if data.starve_timer >= HOME_RELOCATE_HUNT_TIME:
                data.starve_timer = 0.0
                new_home = self._find_prey_rich_home(data, sheep_list)
                if new_home is not None:
                    data.home_x, data.home_y = new_home

            # Shared prey scan (HUNT only)
            data.scan_timer -= dt
            if data.mode == _PackData.HUNT and data.scan_timer <= 0:
                data.scan_timer = SCAN_INTERVAL
                best       = None
                best_score = -1.0
                best_dist  = float('inf')
                for sheep in sheep_list:
                    if not sheep.alive or sheep.dead_state is not None:
                        continue
                    ddx = sheep.tx - avg_x
                    ddy = sheep.ty - avg_y
                    dist_sq = ddx * ddx + ddy * ddy
                    if dist_sq > scan_sq:
                        continue
                    score = 0.0
                    if hasattr(sheep, 'maturity_age') and sheep.age < sheep.maturity_age:
                        score += 3.0
                    max_hp = float(getattr(sheep, 'genetic_hp', 1))
                    if max_hp > 0 and sheep.hp / max_hp < 0.40:
                        score += 4.0
                    if score > best_score or (score == best_score and dist_sq < best_dist):
                        best_score, best, best_dist = score, sheep, dist_sq
                data.hunt_target = best

            # Travel goal
            data.travel_timer = max(0.0, data.travel_timer - dt)
            if data.mode == _PackData.HUNT and data.hunt_target is not None:
                data.travel_x     = data.hunt_target.tx
                data.travel_y     = data.hunt_target.ty
                data.travel_timer = SCAN_INTERVAL
            elif data.travel_timer <= 0:
                if data.mode == _PackData.REST:
                    if n < OPTIMAL_PACK_SIZE:
                        # Small pack: drift toward nearest other pack centre to consolidate
                        nearest = self._nearest_other_pack_center(pid, avg_x, avg_y, 150.0)
                        if nearest is not None:
                            data.travel_x, data.travel_y = nearest
                        else:
                            data.travel_x = data.home_x
                            data.travel_y = data.home_y
                    else:
                        data.travel_x = data.home_x
                        data.travel_y = data.home_y
                    data.travel_timer = 60.0
                else:
                    # Patrol: random direction from pack centre
                    angle = random.uniform(0, 2 * math.pi)
                    r     = random.uniform(20.0, 80.0)
                    data.travel_x     = avg_x + math.cos(angle) * r
                    data.travel_y     = avg_y + math.sin(angle) * r
                    data.travel_timer = random.uniform(TRAVEL_STEP_MIN, TRAVEL_STEP_MAX)

            # Alpha / ranking
            def _dom(w):
                return (w.genetic_strength * 2.0
                        + w.genetic_size * 1.5
                        + (w.hp / max(1.0, w.max_hp)) * 1.0
                        + (1.0 if w.sex == "male" and w.is_adult else 0.0))

            alpha  = max(members, key=_dom)
            ranked = sorted(members, key=_dom, reverse=True)
            data.alpha_id = alpha.wolf_id

            # Push attributes onto every pack member
            for rank_idx, w in enumerate(ranked, start=1):
                w.pack_cx       = data.cx
                w.pack_cy       = data.cy
                w.pack_home_x   = data.home_x
                w.pack_home_y   = data.home_y
                w.pack_home_r   = data.home_radius
                w.pack_mode     = data.mode
                w.pack_target   = data.hunt_target
                w.pack_size     = n
                w.pack_is_alpha = (w.wolf_id == data.alpha_id)
                w.pack_rank     = rank_idx

    # ------------------------------------------------------------------
    # Home base helpers
    # ------------------------------------------------------------------

    def _nearest_other_pack_center(self, own_pid: int, cx: float, cy: float,
                                   max_range: float) -> tuple[float, float] | None:
        """Return the centre of the nearest other pack within max_range, or None."""
        best_dist = max_range
        best_pos  = None
        for pid, other in self._packs.items():
            if pid == own_pid or other.size == 0:
                continue
            dist = math.hypot(other.cx - cx, other.cy - cy)
            if dist < best_dist:
                best_dist = dist
                best_pos  = (other.cx, other.cy)
        return best_pos

    def _find_prey_rich_home(self, data: "_PackData",
                             sheep_list: list) -> tuple[float, float] | None:
        """Find a grass/dirt tile near the nearest sheep cluster, away from rival homes.
        Returns None if no suitable sheep cluster is found."""
        scan_sq = HOME_RELOCATE_SEARCH_R ** 2
        cx, cy  = data.cx, data.cy
        # Find living sheep within range, cluster them by 40-tile cells
        clusters: dict[tuple, list] = {}
        for sheep in sheep_list:
            if not sheep.alive or sheep.dead_state is not None:
                continue
            ddx = sheep.tx - cx
            ddy = sheep.ty - cy
            if ddx * ddx + ddy * ddy > scan_sq:
                continue
            cell = (int(sheep.tx / 40), int(sheep.ty / 40))
            clusters.setdefault(cell, []).append(sheep)
        if not clusters:
            return None
        # Pick the densest cluster
        best_cell = max(clusters, key=lambda k: len(clusters[k]))
        sheep_in_cluster = clusters[best_cell]
        tcx = sum(s.tx for s in sheep_in_cluster) / len(sheep_in_cluster)
        tcy = sum(s.ty for s in sheep_in_cluster) / len(sheep_in_cluster)
        # Find a grass/dirt tile near that cluster, clear of rivals
        hx, hy = self._find_grass_home(tcx, tcy)
        # Check it clears rival homes
        for other in self._packs.values():
            if other is data or other.size == 0 or other.home_x == 0.0:
                continue
            if math.hypot(hx - other.home_x, hy - other.home_y) < HOME_MIN_SEPARATION:
                return None   # too close to a rival — skip this cycle
        return hx, hy

    def _find_grass_home(self, cx: float, cy: float) -> tuple[float, float]:
        """Find the nearest grass or dirt tile to (cx, cy)."""
        grid = self._grid
        if not grid:
            return cx, cy
        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        r0, c0 = int(cy), int(cx)
        if 0 <= r0 < rows and 0 <= c0 < cols and grid[r0][c0] in HOME_GOOD_TILES:
            return cx, cy
        for radius in range(1, 80):
            steps = max(8, radius * 4)
            best_x = best_y = None
            best_d = float('inf')
            for i in range(steps):
                angle = 2 * math.pi * i / steps
                x = cx + math.cos(angle) * radius
                y = cy + math.sin(angle) * radius
                r, c = int(y), int(x)
                if 0 <= r < rows and 0 <= c < cols and grid[r][c] in HOME_GOOD_TILES:
                    d = math.hypot(x - cx, y - cy)
                    if d < best_d:
                        best_d, best_x, best_y = d, x, y
            if best_x is not None:
                return best_x, best_y
        return cx, cy

    def _home_needs_move(self, data: _PackData, grid) -> bool:
        """True if the home is on bad terrain or too close to a rival pack."""
        if grid:
            rows = len(grid)
            cols = len(grid[0]) if rows else 0
            r, c = int(data.home_y), int(data.home_x)
            if 0 <= r < rows and 0 <= c < cols and grid[r][c] in HOME_BAD_TILES:
                return True
        for other in self._packs.values():
            if other is data or other.size == 0:
                continue
            if (other.home_x != 0.0
                    and math.hypot(data.home_x - other.home_x,
                                   data.home_y - other.home_y) < HOME_MIN_SEPARATION):
                return True
            if math.hypot(data.home_x - other.cx,
                          data.home_y - other.cy) < HOME_RIVAL_TOO_CLOSE:
                return True
        return False

    def _find_better_home(self, data: _PackData, grid) -> tuple[float, float]:
        """Spiral out from pack centre to find grass/dirt clear of rival homes."""
        cx, cy = data.cx, data.cy
        rows = len(grid) if grid else 0
        cols = len(grid[0]) if rows else 0
        for radius in range(4, 160, 4):
            steps = max(12, radius * 4)
            candidates = []
            for i in range(steps):
                angle = 2 * math.pi * i / steps
                x = cx + math.cos(angle) * radius
                y = cy + math.sin(angle) * radius
                r, c = int(y), int(x)
                if not (0 <= r < rows and 0 <= c < cols):
                    continue
                if grid[r][c] not in HOME_GOOD_TILES:
                    continue
                ok = True
                for other in self._packs.values():
                    if other is data or other.size == 0:
                        continue
                    if (other.home_x != 0.0
                            and math.hypot(x - other.home_x, y - other.home_y) < HOME_MIN_SEPARATION):
                        ok = False; break
                    if math.hypot(x - other.cx, y - other.cy) < HOME_RIVAL_TOO_CLOSE:
                        ok = False; break
                if ok:
                    candidates.append((x, y))
            if candidates:
                return min(candidates, key=lambda p: math.hypot(p[0] - cx, p[1] - cy))
        return self._find_grass_home(cx, cy)

    # ------------------------------------------------------------------
    # Social hierarchy and exile
    # ------------------------------------------------------------------

    def _dominance_score(self, w) -> float:
        return (w.genetic_strength * 2.0
                + w.genetic_size * 1.5
                + (w.hp / max(1.0, w.max_hp)) * 1.0)

    def _best_exile_direction(self, wolf) -> tuple[float, float]:
        """8-way scan — return direction with most grass/dirt ahead."""
        dirs = [
            (1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0),
            (0.707, 0.707), (0.707, -0.707), (-0.707, 0.707), (-0.707, -0.707),
        ]
        grid = self._grid
        if not grid:
            return random.choice(dirs)
        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        best_dir   = dirs[0]
        best_score = -9999
        for dx, dy in dirs:
            score = 0
            for step in range(1, 16):
                x, y = wolf.tx + dx * step, wolf.ty + dy * step
                r, c = int(y), int(x)
                if not (0 <= r < rows and 0 <= c < cols):
                    score -= 3; break
                tile   = grid[r][c]
                score += (1 if tile in HOME_GOOD_TILES
                          else -2 if tile == SNOW
                          else -1)
            if score > best_score:
                best_score, best_dir = score, (dx, dy)
        return best_dir

    def _expel_wolf(self, loser, aggressors: list):
        loser.was_exiled = True
        loser.pack_id    = -1
        edx, edy = self._best_exile_direction(loser)
        loser.tx += edx * 12.0
        loser.ty += edy * 12.0
        loser.state = "flee"
        loser._flee_timer = 45.0
        if aggressors:
            loser._flee_cx = sum(a.tx for a in aggressors) / len(aggressors)
            loser._flee_cy = sum(a.ty for a in aggressors) / len(aggressors)

    def _exile_weakest(self, same_sex: list, max_attackers: int) -> bool:
        """Dominant members of same_sex gang up on the weakest/lowest-status one.
        Returns True if the victim was expelled."""
        if len(same_sex) < 2:
            return False
        # Victim = lowest status: weakest genetics + lowest pack_rank weight
        def _victim_score(w):
            rank_penalty = getattr(w, "pack_rank", 99) * 0.3
            return self._dominance_score(w) - rank_penalty

        ranked      = sorted(same_sex, key=_victim_score, reverse=True)
        victim      = ranked[-1]    # lowest status
        aggressors  = ranked[:max_attackers]   # top dominants
        dmg = sum(random.uniform(*RATIO_FIGHT_DAMAGE) * max(0.75, a.genetic_strength)
                  for a in aggressors)
        victim.hp = max(0.0, victim.hp - dmg)
        if victim.hp <= victim.max_hp * EXILE_HP_THRESHOLD:
            self._expel_wolf(victim, aggressors)
            return True
        return False

    def _apply_pack_politics(self, dt: float, wolves: list):
        by_pack: dict[int, list] = {}
        for w in wolves:
            if w.pack_id >= 0:
                by_pack.setdefault(w.pack_id, []).append(w)

        for pid, members in by_pack.items():
            data = self._packs.get(pid)
            if data is None:
                continue
            adults  = [w for w in members if w.is_adult]
            if len(adults) < 2:
                continue

            males   = [w for w in adults if w.sex == "male"]
            females = [w for w in adults if w.sex == "female"]
            n_adult = max(1, len(adults))
            m_frac  = len(males)   / n_adult
            f_frac  = len(females) / n_adult

            data.ratio_fight_timer = max(0.0, data.ratio_fight_timer - dt)
            if data.ratio_fight_timer > 0.0:
                continue

            # >85% female → dominant females expel the weakest female
            if f_frac >= FEMALE_EXCESS_FRAC and len(females) >= 3:
                self._exile_weakest(females, max_attackers=4)
                data.ratio_fight_timer = RATIO_FIGHT_INTERVAL

            # >43% male → dominant males expel the weakest male
            elif m_frac >= MALE_EXCESS_FRAC and len(males) >= 2:
                self._exile_weakest(males, max_attackers=3)
                data.ratio_fight_timer = RATIO_FIGHT_INTERVAL

            # Voluntary pair exodus for large packs
            if len(adults) >= EXODUS_MIN_SIZE:
                excess = len(adults) - (EXODUS_MIN_SIZE - 1)
                if random.random() < EXODUS_CHANCE * excess * dt:
                    ranked = sorted(adults, key=self._dominance_score)
                    for wolf in ranked:
                        if wolf.mate_bond_id is None:
                            continue
                        mate = next((w for w in adults
                                     if w.wolf_id == wolf.mate_bond_id), None)
                        if mate is None:
                            continue
                        wolf.pack_id = -1
                        mate.pack_id = -1
                        edx, edy = self._best_exile_direction(wolf)
                        wolf.tx += edx * 8.0; wolf.ty += edy * 8.0
                        mate.tx += edx * 8.0; mate.ty += edy * 8.0
                        break

    # ------------------------------------------------------------------
    # Rival pack and lone wolf pressure
    # ------------------------------------------------------------------

    def _apply_rival_pack_pressure(self, by_pack: dict):
        """Weaker packs flee when they encounter a much stronger one."""
        pids = list(by_pack.keys())
        for i, pid_a in enumerate(pids):
            data_a = self._packs.get(pid_a)
            if data_a is None:
                continue
            for pid_b in pids[i + 1:]:
                data_b = self._packs.get(pid_b)
                if data_b is None:
                    continue
                if math.hypot(data_a.cx - data_b.cx,
                              data_a.cy - data_b.cy) > RIVAL_ENCOUNTER_R:
                    continue
                power_a = sum(self._dominance_score(w) for w in by_pack[pid_a])
                power_b = sum(self._dominance_score(w) for w in by_pack[pid_b])
                if power_a >= power_b * 1.4:
                    for w in by_pack[pid_b]:
                        if w.state not in ("flee", "eat"):
                            w.state       = "flee"
                            w._flee_timer = random.uniform(20.0, 40.0)
                            w._flee_cx    = data_a.cx
                            w._flee_cy    = data_a.cy
                elif power_b >= power_a * 1.4:
                    for w in by_pack[pid_a]:
                        if w.state not in ("flee", "eat"):
                            w.state       = "flee"
                            w._flee_timer = random.uniform(20.0, 40.0)
                            w._flee_cx    = data_b.cx
                            w._flee_cy    = data_b.cy

    def _apply_lone_wolf_pressure(self, wolves: list):
        """Lone wolves scatter when a pack centre comes too close."""
        for w in wolves:
            if w.pack_id >= 0:
                continue
            for data in self._packs.values():
                if data.size == 0:
                    continue
                if math.hypot(w.tx - data.cx, w.ty - data.cy) < LONE_FLEE_R:
                    if w.state not in ("flee", "eat"):
                        w.state       = "flee"
                        w._flee_timer = random.uniform(25.0, 50.0)
                        w._flee_cx    = data.cx
                        w._flee_cy    = data.cy
                    break

    # ------------------------------------------------------------------
    # Visualisation helpers (used by main.py draw_group_overlays)
    # ------------------------------------------------------------------

    def get_pack_territories(self) -> list:
        return [
            {
                "pack_id":     pid,
                "cx":          d.cx,
                "cy":          d.cy,
                "home_x":      d.home_x,
                "home_y":      d.home_y,
                "home_radius": d.home_radius,
                "mode":        d.mode,
                "size":        d.size,
                "name":        d.name,
                "color_idx":   d.color_idx,
                "cells":       [],
            }
            for pid, d in self._packs.items() if d.size > 0
        ]

    def get_active_threats(self) -> list:
        """Return (cx, cy) for every pack currently in HUNT mode with a target."""
        return [
            (d.cx, d.cy)
            for d in self._packs.values()
            if d.hunt_target is not None
        ]
