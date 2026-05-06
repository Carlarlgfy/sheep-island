"""
Wolf — pack predator for Sheep Island.
Structured like the Sheep class. Packs are coordinated by WolfPackManager.
Individual wolves hunt sheep, eat corpses, reproduce, and avoid snow.
"""

import math
import os
import random

import pygame

from mapgen import WATER, GRASS, DIRT, SAND, SNOW, is_walkable_tile, advance_until_blocked

_WOLF_DIR = os.path.join(os.path.dirname(__file__), "fauna", "wolf")

DAY = 300.0   # sim-seconds per in-game day — must match main.py

# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------

# Hunger
WOLF_HUNGER_RATE      = 0.00052   # per second — full→hunt threshold ~1.9 sim-days
WOLF_HUNGER_HUNT      = 0.30      # start hunting above this
WOLF_HUNGER_DESPERATE = 0.70      # ignore all caution above this
WOLF_EAT_RATE         = 0.55      # meat units consumed per second
WOLF_HUNGER_PER_MEAT  = 0.24      # hunger reduced per meat unit consumed
WOLF_EAT_REGEN        = 0.18      # HP/sec healed while eating
WOLF_EAT_STOP_HUNGER  = 0.08      # stop eating when this full
WOLF_MEAL_COOLDOWN    = DAY * 1.0 # personal no-hunt window after a full meal

# HP
WOLF_HP_MIN       = 18
WOLF_HP_MAX       = 40
WOLF_HP_DRAIN     = 1.0 / 30.0   # HP/sec when hunger >= 1.0
WOLF_HP_REGEN     = 0.10         # HP/sec when idle and hunger < 0.35
WOLF_FLEE_HP_FRAC = 0.28         # flee when below this fraction of max HP

# Speed
WOLF_SPEED_MIN  = 5.0
WOLF_SPEED_MAX  = 9.0
WOLF_HUNT_SPEED = 1.30   # speed multiplier while hunting
WOLF_FLEE_SPEED = 1.40   # speed multiplier while fleeing

# Detection
WOLF_HEAR_RADIUS  = 80.0    # hear moving prey within this range
WOLF_SCENT_RADIUS = 25.0    # scent stationary prey within this range
WOLF_SMELL_RADIUS = 120.0   # smell fresh corpses within this range
WOLF_SCARE_RADIUS    = 40.0    # sheep become afraid within this range
WOLF_SCARE_DUR       = 18.0    # seconds sheep stay afraid after wolf passes
WOLF_SCARE_DURATION  = WOLF_SCARE_DUR   # alias used by herd.py

# Combat
WOLF_ATTACK_RANGE    = 1.5
WOLF_ATTACK_COOLDOWN = 2.2
WOLF_DAMAGE_MIN      = 3.5
WOLF_DAMAGE_MAX      = 8.0
WOLF_LUNGE_DURATION  = 0.45   # seconds the lunge animation plays

# Separation between wolves
WOLF_SEP_RADIUS = 1.0
WOLF_SEP_FORCE  = 2.0

# Reproduction
WOLF_REPRODUCE_COOLDOWN = 1400.0   # ~4.7 days between litters
WOLF_GESTATION          = 1000.0   # ~3.3 days gestation
WOLF_LITTER_MIN         = 3
WOLF_LITTER_MAX         = 6
WOLF_PUP_MORTALITY      = 0.12     # fraction of pups that die at birth
WOLF_REPRODUCE_HUNGER   = 0.40     # must be below this hunger to mate
WOLF_MATE_RADIUS        = 15.0     # tiles within which mating can occur
WOLF_MATURITY_AGE       = 1100.0   # ~3.7 days to reach adulthood

# Lifespan
WOLF_LIFESPAN_MIN = 6000.0    # 20 sim-days
WOLF_LIFESPAN_MAX = 12000.0   # 40 sim-days

# Genetics ranges (multiplicative around 1.0)
WOLF_SIZE_RANGE     = 0.18
WOLF_STRENGTH_RANGE = 0.20
WOLF_SPEED_RANGE    = 0.16
WOLF_LIFESPAN_RANGE = 0.10

# Corpse timers
WOLF_CORPSE_FRESH_MIN   = DAY * 1
WOLF_CORPSE_FRESH_MAX   = DAY * 2
WOLF_CORPSE_DECAYED_MIN = DAY * 1
WOLF_CORPSE_DECAYED_MAX = DAY * 2

# Display
WOLF_BASE_SCALE    = 1.20
WOLF_MALE_MULT     = 1.25
WOLF_SIZE_HP_SCALE = 0.22

# Snow exposure
WOLF_SNOW_THRESHOLD = 450.0   # seconds on snow before damage starts
WOLF_SNOW_DAMAGE    = 0.20    # HP/sec once threshold exceeded


class Wolf:
    IDLE = "idle"
    WALK = "walk"
    HUNT = "hunt"
    EAT  = "eat"
    FLEE = "flee"

    _sprites_raw: dict | None = None
    _cache: dict = {}
    _avg_colors: dict = {}
    _next_id = 0

    LOD_THRESHOLD = 6.0

    # ------------------------------------------------------------------
    # Sprite loading
    # ------------------------------------------------------------------

    @classmethod
    def load_sprites(cls):
        if cls._sprites_raw is not None:
            return

        def _load(name):
            return pygame.image.load(os.path.join(_WOLF_DIR, name)).convert_alpha()

        right     = _load("right side facing female gray wolf.png")
        left      = pygame.transform.flip(right, True, False)
        front     = _load("standing female gray wolf.png")
        behind    = _load("backward facing gray female wolf.png")
        sit_right = _load("sitting wolf facing right.png")
        sit_left  = pygame.transform.flip(sit_right, True, False)
        sit_front = _load("sitting gray wolf.png")
        eat_right = _load("gray female wolf rght facing eating.png")
        eat_left  = pygame.transform.flip(eat_right, True, False)
        eat_front = _load("gray female facing forward eating.png")
        lunge_r   = _load("gray female wolf lunging .png")
        lunge_l   = pygame.transform.flip(lunge_r, True, False)
        dead_r    = _load("dead wolf facing right.png")
        dead_l    = pygame.transform.flip(dead_r, True, False)
        decay_r   = _load("decaying femal wolf corpse facing right.png")
        decay_l   = pygame.transform.flip(decay_r, True, False)

        cls._sprites_raw = {
            "right":          right,     "left":          left,
            "front":          front,     "behind":        behind,
            "idle_right":     sit_right, "idle_left":     sit_left,
            "idle_front":     sit_front, "idle_behind":   behind,
            "eat_right":      eat_right, "eat_left":      eat_left,
            "eat_front":      eat_front, "eat_behind":    eat_front,
            "lunge_right":    lunge_r,   "lunge_left":    lunge_l,
            "dead_right":     dead_r,    "dead_left":     dead_l,
            "decayed_right":  decay_r,   "decayed_left":  decay_l,
        }
        cls._cache = {}
        cls._avg_colors = {k: cls._sample_avg_color(v) for k, v in cls._sprites_raw.items()}

    @classmethod
    def _sample_avg_color(cls, surf: pygame.Surface) -> tuple:
        w, h = surf.get_size()
        xs = [int(w * (i + 0.5) / 10) for i in range(10)]
        ys = [int(h * (j + 0.5) / 10) for j in range(10)]
        r_s = g_s = b_s = cnt = 0
        for x in xs:
            for y in ys:
                c = surf.get_at((x, y))
                if c.a > 32:
                    r_s += c.r; g_s += c.g; b_s += c.b; cnt += 1
        return (r_s // cnt, g_s // cnt, b_s // cnt) if cnt else (170, 150, 120)

    @classmethod
    def _scaled(cls, key: str, tile_size: float) -> pygame.Surface:
        ts = max(1, round(tile_size))
        if ts not in cls._cache:
            target_h = max(4, round(ts * 2.6))
            entry = {}
            for k, surf in cls._sprites_raw.items():
                ow, oh = surf.get_size()
                nw = max(1, int(ow * target_h / oh))
                entry[k] = pygame.transform.scale(surf, (nw, target_h))
            cls._cache[ts] = entry
        return cls._cache[ts][key]

    # ------------------------------------------------------------------
    # Constructor
    # ------------------------------------------------------------------

    def __init__(self, tile_x: float, tile_y: float,
                 age: float = None, sex: str = None,
                 genetic_size: float = None,
                 genetic_speed: float = None,
                 genetic_strength: float = None,
                 genetic_hp: int = None,
                 genetic_lifespan: float = None,
                 mother_id: int = None,
                 father_id: int = None):

        self.tx = float(tile_x)
        self.ty = float(tile_y)
        self.dx = 0.0
        self.dy = 0.0
        self.facing = "right"
        self.state  = Wolf.IDLE
        self.timer  = random.uniform(2.0, 5.0)

        self.sex = sex if sex in ("male", "female") else (
            "male" if random.random() < 0.5 else "female"
        )
        self.wolf_id  = Wolf._next_id
        Wolf._next_id += 1
        self.mother_id = mother_id
        self.father_id = father_id

        # Genetics
        def _cg(val, r):
            return max(1.0 - r, min(1.0 + r, val))

        self.genetic_size = (
            _cg(genetic_size, WOLF_SIZE_RANGE) if genetic_size is not None
            else random.uniform(1.0 - WOLF_SIZE_RANGE, 1.0 + WOLF_SIZE_RANGE)
        )
        self.genetic_strength = (
            _cg(genetic_strength, WOLF_STRENGTH_RANGE) if genetic_strength is not None
            else _cg(random.uniform(1.0 - WOLF_STRENGTH_RANGE, 1.0 + WOLF_STRENGTH_RANGE)
                     + (self.genetic_size - 1.0) * 0.22, WOLF_STRENGTH_RANGE)
        )
        self.genetic_speed = (
            _cg(float(genetic_speed), WOLF_SPEED_RANGE) if genetic_speed is not None
            else _cg(random.uniform(1.0 - WOLF_SPEED_RANGE, 1.0 + WOLF_SPEED_RANGE)
                     - (self.genetic_size - 1.0) * 0.25, WOLF_SPEED_RANGE)
        )
        self.genetic_hp = (
            int(max(WOLF_HP_MIN, min(WOLF_HP_MAX, genetic_hp))) if genetic_hp is not None
            else int(round(max(WOLF_HP_MIN, min(WOLF_HP_MAX,
                random.randint(WOLF_HP_MIN, WOLF_HP_MAX)
                + (self.genetic_size - 1.0) * 14.0
                + (self.genetic_strength - 1.0) * 8.0
            ))))
        )
        self.genetic_lifespan = (
            _cg(genetic_lifespan, WOLF_LIFESPAN_RANGE) if genetic_lifespan is not None
            else random.uniform(1.0 - WOLF_LIFESPAN_RANGE, 1.0 + WOLF_LIFESPAN_RANGE)
        )

        self.maturity_age = WOLF_MATURITY_AGE
        self.lifespan = random.uniform(WOLF_LIFESPAN_MIN, WOLF_LIFESPAN_MAX) * self.genetic_lifespan
        self.age      = float(age) if age is not None else random.uniform(
            self.maturity_age, self.maturity_age * 2.5)

        self.hp     = float(self.max_hp)
        self.hunger = random.uniform(0.05, 0.40)

        # Reproduction
        self.pregnant             = False
        self.gestation_timer      = 0.0
        self._pending_litter: list = []
        self.reproduce_cooldown   = random.uniform(0, WOLF_REPRODUCE_COOLDOWN * 0.2)
        self.mate_bond_id: int | None = None
        self.reproductive_success = 0

        # Hunt state
        self._hunt_target  = None    # ref to Sheep being hunted or corpse being eaten
        self._attack_cooldown = 0.0
        self._scan_timer   = random.uniform(0, 3.5)
        self._lunge_timer  = 0.0
        self._meal_cooldown = 0.0

        # Flee state
        self._flee_timer = 0.0
        self._flee_cx    = tile_x
        self._flee_cy    = tile_y

        # Pack info — written each frame by WolfPackManager
        self.pack_id      = -1
        self.pack_cx      = tile_x
        self.pack_cy      = tile_y
        self.pack_home_x  = tile_x
        self.pack_home_y  = tile_y
        self.pack_home_r  = 10.0
        self.pack_mode    = "rest"   # "rest" | "hunt"
        self.pack_target  = None     # shared prey target from WolfPackManager
        self.pack_size    = 1
        self.pack_is_alpha = True
        self.pack_rank    = 1
        self.was_exiled   = False

        # Corpse
        self.alive       = True
        self.dead_state  = None   # None | "fresh" | "decayed"
        self.death_timer = 0.0
        self.death_facing = "right"

        # Snow exposure
        self.snow_exposure = 0.0

        # Pre-filtered neighbour lists — written by ProximityScanner each frame
        self.nearby_sheep:  list = []
        self.nearby_wolves: list = []

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_adult(self) -> bool:
        return self.age >= self.maturity_age

    @property
    def is_beta(self) -> bool:
        return False   # no beta state in rewrite

    @property
    def size_scale(self) -> float:
        growth   = 0.55 + 0.45 * min(1.0, self.age / self.maturity_age)
        sex_mult = WOLF_MALE_MULT if self.sex == "male" else 1.0
        return growth * self.genetic_size * sex_mult * WOLF_BASE_SCALE

    @property
    def adult_size_scale(self) -> float:
        sex_mult = WOLF_MALE_MULT if self.sex == "male" else 1.0
        return self.genetic_size * sex_mult

    @property
    def max_hp(self) -> float:
        size_mult = 1.0 + (self.adult_size_scale - 1.0) * WOLF_SIZE_HP_SCALE
        return max(8.0, float(self.genetic_hp) * size_mult)

    @property
    def move_speed(self) -> float:
        base = (WOLF_SPEED_MIN + WOLF_SPEED_MAX) * 0.5
        return max(WOLF_SPEED_MIN * 0.8, min(WOLF_SPEED_MAX * 1.1, base * self.genetic_speed))

    @property
    def collision_radius(self) -> float:
        return 0.45 + self.size_scale * 0.40

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _refresh_facing(self):
        if abs(self.dx) >= abs(self.dy):
            self.facing = "right" if self.dx >= 0 else "left"
        else:
            self.facing = "front" if self.dy > 0 else "behind"

    def _face_toward(self, tx: float, ty: float):
        ddx, ddy = tx - self.tx, ty - self.ty
        if abs(ddx) >= abs(ddy):
            self.facing = "right" if ddx >= 0 else "left"
        else:
            self.facing = "front" if ddy > 0 else "behind"

    def related_to(self, other: "Wolf") -> bool:
        if other is None or self.wolf_id == other.wolf_id:
            return True
        ps = {p for p in (self.mother_id, self.father_id) if p is not None}
        po = {p for p in (other.mother_id, other.father_id) if p is not None}
        if self.wolf_id in po or other.wolf_id in ps:
            return True
        return bool(ps and ps & po)

    def genetic_similarity(self, other: "Wolf") -> float:
        diffs = (
            abs(self.genetic_size     - other.genetic_size)     / max(0.01, WOLF_SIZE_RANGE     * 2),
            abs(self.genetic_strength - other.genetic_strength) / max(0.01, WOLF_STRENGTH_RANGE * 2),
            abs(self.genetic_speed    - other.genetic_speed)    / max(0.01, WOLF_SPEED_RANGE    * 2),
        )
        return max(0.0, 1.0 - sum(diffs) / len(diffs))

    # ------------------------------------------------------------------
    # Movement helpers
    # ------------------------------------------------------------------

    def _separation_delta(self, dt: float) -> tuple:
        sx = sy = 0.0
        for other, _ in self.nearby_wolves:
            if other.dead_state is not None:
                continue
            ddx = self.tx - other.tx
            ddy = self.ty - other.ty
            dist = math.sqrt(ddx * ddx + ddy * ddy)
            if 0 < dist < WOLF_SEP_RADIUS:
                strength = (WOLF_SEP_RADIUS - dist) / WOLF_SEP_RADIUS
                sx += (ddx / dist) * strength * WOLF_SEP_FORCE * dt
                sy += (ddy / dist) * strength * WOLF_SEP_FORCE * dt
        return sx, sy

    def _home_pull_delta(self, dt: float) -> tuple:
        """Pull toward pack home when in REST mode and outside home radius.
        Small packs pull home less so they can drift toward other packs to consolidate."""
        if self.pack_mode != "rest":
            return 0.0, 0.0
        ddx = self.pack_home_x - self.tx
        ddy = self.pack_home_y - self.ty
        dist = math.hypot(ddx, ddy)
        if dist < self.pack_home_r or dist < 0.001:
            return 0.0, 0.0
        t    = min(1.0, (dist - self.pack_home_r) / max(1.0, self.pack_home_r * 2.0))
        # Small packs anchor weakly so they can drift toward other groups
        base = 3.5 if self.pack_size < 10 else 6.0
        pull = base * t * dt
        return (ddx / dist) * pull, (ddy / dist) * pull

    def _pack_cohesion_delta(self, dt: float) -> tuple:
        """Pull toward pack centre. Small packs cohese much more tightly."""
        if self.pack_size <= 1:
            return 0.0, 0.0
        ddx = self.pack_cx - self.tx
        ddy = self.pack_cy - self.ty
        dist = math.hypot(ddx, ddy)
        if dist < 4.0 or dist < 0.001:
            return 0.0, 0.0
        t    = min(1.0, (dist - 4.0) / 12.0)
        # Under-10 packs pull together very strongly to stay viable
        base = 16.0 if self.pack_size < 10 else 8.0
        pull = base * t * dt
        if self.pack_mode == "rest":
            pull *= 1.5
        return (ddx / dist) * pull, (ddy / dist) * pull

    def _bad_terrain_avoidance_delta(self, grid: list, dt: float) -> tuple:
        """Push wolf away from snow (cold damage) and sand/water (beach stranding)."""
        if not grid:
            return 0.0, 0.0
        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        sx = sy = 0.0
        for dr in range(-5, 6, 2):
            for dc in range(-5, 6, 2):
                r = int(self.ty) + dr
                c = int(self.tx) + dc
                if not (0 <= r < rows and 0 <= c < cols):
                    continue
                tile = grid[r][c]
                if tile == SNOW:
                    push = 5.0
                elif tile in (SAND, WATER):
                    push = 14.0   # strong enough to overcome hunt/flee momentum
                else:
                    continue
                dist = math.hypot(float(dc), float(dr)) or 0.01
                w    = max(0.0, 1.0 - dist / 5.0) * push * dt
                sx  -= (dc / dist) * w
                sy  -= (dr / dist) * w
        return sx, sy

    def _best_flee_direction(self, grid: list) -> tuple:
        """8-way scan — return direction with the most grass/dirt tiles."""
        dirs = [
            (1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0),
            (0.707, 0.707), (0.707, -0.707), (-0.707, 0.707), (-0.707, -0.707),
        ]
        if not grid:
            return random.choice(dirs)
        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        GOOD = {GRASS, DIRT}
        best_dir   = dirs[0]
        best_score = -9999
        for dx, dy in dirs:
            score = 0
            for step in range(1, 13):
                x, y = self.tx + dx * step, self.ty + dy * step
                r, c = int(y), int(x)
                if not (0 <= r < rows and 0 <= c < cols):
                    score -= 3; break
                tile   = grid[r][c]
                score += 1 if tile in GOOD else (-2 if tile == SNOW else -1)
            if score > best_score:
                best_score, best_dir = score, (dx, dy)
        return best_dir

    def _move_toward(self, tx: float, ty: float, dt: float, grid: list,
                     speed_mult: float = 1.0):
        ddx  = tx - self.tx
        ddy  = ty - self.ty
        dist = math.sqrt(ddx * ddx + ddy * ddy)
        if dist > 0.05:
            self.dx = ddx / dist
            self.dy = ddy / dist
        self._refresh_facing()
        sx, sy = self._separation_delta(dt)
        spd    = self.move_speed * speed_mult
        nx     = self.tx + self.dx * spd * dt + sx
        ny     = self.ty + self.dy * spd * dt + sy
        self.tx, self.ty, _ = advance_until_blocked(grid, self.tx, self.ty, nx, ny)

    # ------------------------------------------------------------------
    # Prey detection
    # ------------------------------------------------------------------

    def _scare_nearby_sheep(self):
        scare_sq = WOLF_SCARE_RADIUS ** 2
        for sheep, _ in self.nearby_sheep:
            if sheep.dead_state is not None or not sheep.alive:
                continue
            ddx = sheep.tx - self.tx
            ddy = sheep.ty - self.ty
            dist_sq = ddx * ddx + ddy * ddy
            if dist_sq > scare_sq or dist_sq == 0:
                continue
            dist = math.sqrt(dist_sq)
            sheep.wolf_aware       = True
            sheep._wolf_fear_timer = WOLF_SCARE_DUR
            sheep.wolf_flee_dx     = (sheep.tx - self.tx) / dist
            sheep.wolf_flee_dy     = (sheep.ty - self.ty) / dist

    def _score_prey(self, sheep) -> float:
        score = 0.0
        if not hasattr(sheep, 'maturity_age'):
            return score
        if sheep.age < sheep.maturity_age:
            score += 4.0
        elif sheep.age > sheep.lifespan * 0.55:
            score += 3.0
        max_hp = float(getattr(sheep, 'max_hp', sheep.genetic_hp))
        if max_hp > 0:
            hp_frac = sheep.hp / max_hp
            if hp_frac < 0.30:
                score += 5.0
            elif hp_frac < 0.60:
                score += 2.0
        if getattr(sheep, 'herd_id', -1) < 0:
            score += 4.0
        return score

    def _find_best_prey(self):
        hear_sq  = WOLF_HEAR_RADIUS  ** 2
        scent_sq = WOLF_SCENT_RADIUS ** 2
        best_score = -1.0
        best       = None
        best_dist  = float('inf')
        for sheep, _ in self.nearby_sheep:
            if sheep.dead_state is not None or not sheep.alive:
                continue
            ddx = sheep.tx - self.tx
            ddy = sheep.ty - self.ty
            dist_sq = ddx * ddx + ddy * ddy
            moving  = (abs(getattr(sheep, 'dx', 0.0)) + abs(getattr(sheep, 'dy', 0.0))) > 0.01
            if dist_sq > (hear_sq if moving else scent_sq):
                continue
            score = self._score_prey(sheep)
            if score > best_score or (score == best_score and dist_sq < best_dist):
                best_score, best, best_dist = score, sheep, dist_sq
        return best

    def _find_nearest_corpse(self):
        if self._meal_cooldown > 0.0 or self.hunger < 0.65:
            return None
        smell_sq = WOLF_SMELL_RADIUS ** 2
        best_dist = float('inf')
        best      = None
        for sheep, _ in self.nearby_sheep:
            if sheep.dead_state != "fresh":
                continue
            if getattr(sheep, 'meat_value', 0.0) <= 0:
                continue
            ddx = sheep.tx - self.tx
            ddy = sheep.ty - self.ty
            dist_sq = ddx * ddx + ddy * ddy
            if dist_sq < smell_sq and dist_sq < best_dist:
                best_dist, best = dist_sq, sheep
        return best

    # ------------------------------------------------------------------
    # Combat
    # ------------------------------------------------------------------

    def _do_attack(self, target) -> bool:
        """Deal damage to target. Returns True if target died."""
        dmg = self.genetic_strength * random.uniform(WOLF_DAMAGE_MIN, WOLF_DAMAGE_MAX)
        target.hp = max(0.0, target.hp - dmg)
        from ram import Ram
        if isinstance(target, Ram) and target.dead_state is None:
            counter   = getattr(target, 'genetic_strength', 1.0) * random.uniform(1.5, 4.0)
            self.hp   = max(0.0, self.hp - counter)
        if target.hp <= 0:
            target._die()
            return True
        return False

    # ------------------------------------------------------------------
    # Reproduction
    # ------------------------------------------------------------------

    def _try_reproduce(self, wolf_list: list, new_wolves: list):
        if not self.is_adult or self.sex != "female":
            return
        if self.pregnant or self.reproduce_cooldown > 0 or self.hunger >= WOLF_REPRODUCE_HUNGER:
            return
        mate_sq    = WOLF_MATE_RADIUS ** 2
        best       = None
        best_score = -1.0
        for other, dist_sq in self.nearby_wolves:
            if (not other.alive or other.dead_state is not None
                    or not other.is_adult or other.sex != "male"
                    or other.reproduce_cooldown > 0
                    or other.hunger >= WOLF_REPRODUCE_HUNGER
                    or self.related_to(other)):
                continue
            if dist_sq > mate_sq:
                continue
            score = 1.0 - other.hunger
            if self.mate_bond_id == other.wolf_id:
                score += 3.0
            if self.pack_id >= 0 and other.pack_id == self.pack_id:
                score += 0.5
            if score > best_score:
                best_score, best = score, other
        if best is None:
            return

        other = best
        self.mate_bond_id  = other.wolf_id
        other.mate_bond_id = self.wolf_id

        def _inherit(attr: str, r: float) -> float:
            mid = (getattr(self, attr) + getattr(other, attr)) / 2.0
            return max(1.0 - r, min(1.0 + r, mid + random.gauss(0, r * 0.12)))

        raw      = random.randint(WOLF_LITTER_MIN, WOLF_LITTER_MAX)
        survived = sum(1 for _ in range(raw) if random.random() > WOLF_PUP_MORTALITY)
        count    = max(1, survived)
        pending  = []
        for _ in range(count):
            pending.append({
                "sex":      "male" if random.random() < 0.5 else "female",
                "size":     _inherit("genetic_size",     WOLF_SIZE_RANGE),
                "strength": _inherit("genetic_strength", WOLF_STRENGTH_RANGE),
                "speed":    _inherit("genetic_speed",    WOLF_SPEED_RANGE),
                "hp":       int(round(max(WOLF_HP_MIN, min(WOLF_HP_MAX,
                                (self.genetic_hp + other.genetic_hp) / 2.0
                                + random.gauss(0, 1.5))))),
                "lifespan": _inherit("genetic_lifespan", WOLF_LIFESPAN_RANGE),
            })
        self.pregnant          = True
        self.gestation_timer   = WOLF_GESTATION
        self._pending_litter   = pending
        self.reproductive_success  += count
        other.reproductive_success += count
        self.reproduce_cooldown    = WOLF_REPRODUCE_COOLDOWN
        other.reproduce_cooldown   = WOLF_REPRODUCE_COOLDOWN * 0.5
        self.hunger  = min(1.0, self.hunger + 0.03 * count)
        other.hunger = min(1.0, other.hunger + 0.02)

    def _birth(self, grid: list, new_wolves: list):
        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        for data in self._pending_litter:
            for _ in range(8):
                ox = self.tx + random.uniform(-2.0, 2.0)
                oy = self.ty + random.uniform(-2.0, 2.0)
                if is_walkable_tile(grid, int(oy), int(ox)):
                    pup = Wolf(ox, oy, age=0.0,
                               sex=data["sex"],
                               genetic_size=data["size"],
                               genetic_speed=data["speed"],
                               genetic_strength=data["strength"],
                               genetic_hp=data["hp"],
                               genetic_lifespan=data["lifespan"],
                               mother_id=self.wolf_id,
                               father_id=self.mate_bond_id)
                    pup.hunger  = 0.0
                    pup.pack_id = self.pack_id
                    new_wolves.append(pup)
                    break

    # ------------------------------------------------------------------
    # Death / corpse
    # ------------------------------------------------------------------

    def _die(self):
        self.dead_state   = "fresh"
        self.death_timer  = random.uniform(WOLF_CORPSE_FRESH_MIN, WOLF_CORPSE_FRESH_MAX)
        self.death_facing = self.facing
        self.state        = Wolf.IDLE
        self.dx = self.dy = 0.0
        self.pack_id      = -1
        self.pregnant     = False
        self._pending_litter = []
        self._hunt_target = None

    def _update_corpse(self, dt: float):
        self.death_timer -= dt
        if self.death_timer <= 0:
            if self.dead_state == "fresh":
                self.dead_state  = "decayed"
                self.death_timer = random.uniform(WOLF_CORPSE_DECAYED_MIN, WOLF_CORPSE_DECAYED_MAX)
            elif self.dead_state == "decayed":
                self.alive = False

    # ------------------------------------------------------------------
    # Eating helpers
    # ------------------------------------------------------------------

    def _finish_eating(self):
        well_fed = self.hunger < 0.25
        self._hunt_target = None
        if well_fed:
            self.hunger = min(self.hunger, 0.05)
            self._meal_cooldown = random.uniform(
                WOLF_MEAL_COOLDOWN * 0.8, WOLF_MEAL_COOLDOWN * 1.3)
        self.state = Wolf.IDLE
        self.timer = random.uniform(3.0, 8.0)

    # ------------------------------------------------------------------
    # Main update — mirrors Sheep.update structure
    # ------------------------------------------------------------------

    def update(self, dt: float, grid: list, sheep_list: list,
               wolf_list: list, new_wolves: list):
        if not self.alive:
            return
        if self.dead_state is not None:
            self._update_corpse(dt)
            return

        # --- Timers ---
        self.age   += dt
        self.timer -= dt
        self._attack_cooldown  = max(0.0, self._attack_cooldown  - dt)
        self._meal_cooldown    = max(0.0, self._meal_cooldown    - dt)
        self._scan_timer       = max(0.0, self._scan_timer       - dt)
        self._lunge_timer      = max(0.0, self._lunge_timer      - dt)
        self.reproduce_cooldown = max(0.0, self.reproduce_cooldown - dt)

        # --- Hunger & HP ---
        if self.state != Wolf.EAT:
            self.hunger = min(1.0, self.hunger + WOLF_HUNGER_RATE * self.genetic_size * dt)
        if self.hunger >= 1.0:
            self.hp = max(0.0, self.hp - WOLF_HP_DRAIN * dt)
        if self.state == Wolf.IDLE and self.hunger < 0.35:
            self.hp = min(self.max_hp, self.hp + WOLF_HP_REGEN * dt)

        # --- Snow exposure ---
        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        r0, c0 = int(self.ty), int(self.tx)
        on_snow = 0 <= r0 < rows and 0 <= c0 < cols and grid[r0][c0] == SNOW
        self.snow_exposure = (self.snow_exposure + dt) if on_snow else 0.0
        if on_snow and self.snow_exposure >= WOLF_SNOW_THRESHOLD:
            self.hp = max(0.0, self.hp - WOLF_SNOW_DAMAGE * dt)

        # --- Death checks ---
        if self.age >= self.lifespan or self.hp <= 0:
            self._die()
            return

        # --- Gestation ---
        if self.pregnant:
            self.gestation_timer -= dt
            if self.gestation_timer <= 0:
                self._birth(grid, new_wolves)
                self.pregnant        = False
                self._pending_litter = []

        # --- Validate hunt target ---
        if self._hunt_target is not None:
            t = self._hunt_target
            if not t.alive or t.dead_state not in (None, "fresh"):
                self._hunt_target = None
                if self.state == Wolf.HUNT:
                    self.state = Wolf.WALK
                    self.timer = random.uniform(3.0, 6.0)

        # ================================================================
        # State machine
        # ================================================================

        # ── FLEE ────────────────────────────────────────────────────────
        if self.state == Wolf.FLEE:
            self._flee_timer -= dt
            fx = self.tx - self._flee_cx
            fy = self.ty - self._flee_cy
            fd = math.hypot(fx, fy)
            if fd > 0.5:
                self.dx, self.dy = fx / fd, fy / fd
            else:
                self.dx, self.dy = self._best_flee_direction(grid)
            self._refresh_facing()
            sx, sy = self._separation_delta(dt)
            nx = self.tx + self.dx * self.move_speed * WOLF_FLEE_SPEED * dt + sx
            ny = self.ty + self.dy * self.move_speed * WOLF_FLEE_SPEED * dt + sy
            self.tx, self.ty, _ = advance_until_blocked(grid, self.tx, self.ty, nx, ny)
            if self._flee_timer <= 0 and self.hp >= self.max_hp * 0.45:
                self.state = Wolf.IDLE
                self.timer = random.uniform(2.0, 5.0)
            return

        # ── EAT ─────────────────────────────────────────────────────────
        if self.state == Wolf.EAT:
            corpse = self._hunt_target
            if (corpse is None or corpse.dead_state != "fresh"
                    or getattr(corpse, 'meat_value', 0.0) <= 0):
                self._finish_eating()
                return
            # Approach corpse
            ddx  = corpse.tx - self.tx
            ddy  = corpse.ty - self.ty
            dist = math.hypot(ddx, ddy)
            if dist > 1.2:
                self._move_toward(corpse.tx, corpse.ty, dt, grid, 0.5)
                return
            # Face the corpse and eat
            self._face_toward(corpse.tx, corpse.ty)
            bite      = WOLF_EAT_RATE * dt
            consumed  = min(bite, corpse.meat_value)
            corpse.meat_value -= consumed
            self.hunger = max(0.0, self.hunger - consumed * WOLF_HUNGER_PER_MEAT)
            self.hp     = min(self.max_hp, self.hp + WOLF_EAT_REGEN * dt)
            if (self.hunger <= WOLF_EAT_STOP_HUNGER
                    or corpse.meat_value <= 0 or corpse.dead_state != "fresh"):
                if corpse.meat_value <= 0 and corpse.dead_state == "fresh":
                    corpse.dead_state  = "decayed"
                    corpse.death_timer = random.uniform(30.0, 90.0)
                self._finish_eating()
            return

        # ── HUNT ────────────────────────────────────────────────────────
        if self.state == Wolf.HUNT:
            # Flee if badly hurt
            if self.hp < self.max_hp * WOLF_FLEE_HP_FRAC:
                ref = self._hunt_target
                self.state       = Wolf.FLEE
                self._flee_timer = 25.0
                self._flee_cx    = ref.tx if ref else self.pack_cx
                self._flee_cy    = ref.ty if ref else self.pack_cy
                self._hunt_target = None
                return

            # Periodic rescan
            if self._scan_timer <= 0:
                self._scan_timer = 3.5
                corpse = self._find_nearest_corpse()
                if corpse is not None:
                    self._hunt_target = corpse
                elif (self.pack_target is not None
                      and self.pack_target.alive
                      and self.pack_target.dead_state is None):
                    self._hunt_target = self.pack_target
                else:
                    prey = self._find_best_prey()
                    if prey is not None:
                        self._hunt_target = prey

            target = self._hunt_target
            if target is None:
                self.state = Wolf.WALK
                self.timer = random.uniform(3.0, 6.0)
                return

            # Switch to EAT if target became a fresh corpse
            if target.dead_state == "fresh" and getattr(target, 'meat_value', 0.0) > 0:
                self.state = Wolf.EAT
                return

            self._scare_nearby_sheep()

            ddx  = target.tx - self.tx
            ddy  = target.ty - self.ty
            dist = math.hypot(ddx, ddy)

            if dist <= WOLF_ATTACK_RANGE and self._attack_cooldown <= 0:
                # Lunge attack
                self._lunge_timer     = WOLF_LUNGE_DURATION
                self._attack_cooldown = WOLF_ATTACK_COOLDOWN
                self._face_toward(target.tx, target.ty)
                killed = self._do_attack(target)
                if killed:
                    if (target.dead_state == "fresh"
                            and getattr(target, 'meat_value', 0.0) > 0):
                        self.state = Wolf.EAT
                    else:
                        self._hunt_target = None
                        self.state = Wolf.WALK
                        self.timer = random.uniform(3.0, 6.0)
                return

            self._move_toward(target.tx, target.ty, dt, grid, WOLF_HUNT_SPEED)
            return

        # ── IDLE / WALK ─────────────────────────────────────────────────

        # Reproduce during REST when conditions are met
        if (self.pack_mode == "rest" and self.is_adult and self.sex == "female"
                and not self.pregnant and self.reproduce_cooldown <= 0
                and self.hunger < WOLF_REPRODUCE_HUNGER):
            self._try_reproduce(wolf_list, new_wolves)

        # Decide whether to start hunting
        wants_food    = self.hunger >= WOLF_HUNGER_HUNT and self._meal_cooldown <= 0.0
        pack_hunting  = self.pack_mode == "hunt"
        desperate     = self.hunger >= WOLF_HUNGER_DESPERATE
        lone          = self.pack_size <= 1
        should_hunt   = wants_food and (pack_hunting or desperate or lone)

        if should_hunt and self._scan_timer <= 0:
            self._scan_timer = 3.5
            corpse = self._find_nearest_corpse()
            if corpse is not None:
                self._hunt_target = corpse
                self.state = Wolf.HUNT
                return
            if (self.pack_target is not None
                    and self.pack_target.alive
                    and self.pack_target.dead_state is None):
                self._hunt_target = self.pack_target
                self.state = Wolf.HUNT
                return
            prey = self._find_best_prey()
            if prey is not None:
                self._hunt_target = prey
                self.state = Wolf.HUNT
                return

        # State transitions (idle ↔ walk)
        if self.timer <= 0:
            if self.state == Wolf.IDLE:
                # Choose walk direction based on pack mode
                if self.pack_mode == "hunt" and self.pack_target is not None:
                    ddx  = self.pack_target.tx - self.tx
                    ddy  = self.pack_target.ty - self.ty
                    dist = math.hypot(ddx, ddy)
                    if dist > 0.001:
                        self.dx, self.dy = ddx / dist, ddy / dist
                    else:
                        angle = random.uniform(0, 2 * math.pi)
                        self.dx, self.dy = math.cos(angle), math.sin(angle)
                elif self.pack_mode == "rest":
                    ddx  = self.pack_home_x - self.tx
                    ddy  = self.pack_home_y - self.ty
                    dist = math.hypot(ddx, ddy)
                    if dist > self.pack_home_r and dist > 0.001:
                        self.dx, self.dy = ddx / dist, ddy / dist
                    else:
                        angle = random.uniform(0, 2 * math.pi)
                        self.dx, self.dy = math.cos(angle), math.sin(angle)
                else:
                    angle = random.uniform(0, 2 * math.pi)
                    self.dx, self.dy = math.cos(angle), math.sin(angle)
                self._refresh_facing()
                self.state = Wolf.WALK
                self.timer = random.uniform(4.0, 10.0)
            else:
                self.state = Wolf.IDLE
                self.timer = random.uniform(2.0, 6.0)
                self.dx = self.dy = 0.0

        if self.state == Wolf.WALK:
            sx, sy = self._separation_delta(dt)
            hx, hy = self._home_pull_delta(dt)
            px, py = self._pack_cohesion_delta(dt)
            wx, wy = self._bad_terrain_avoidance_delta(grid, dt)
            drift  = self.move_speed * (0.55 if self.pack_mode == "rest" else 0.80)
            nx = self.tx + self.dx * drift * dt + sx + hx + px + wx
            ny = self.ty + self.dy * drift * dt + sy + hy + py + wy
            old_tx, old_ty = self.tx, self.ty
            self.tx, self.ty, blocked = advance_until_blocked(grid, self.tx, self.ty, nx, ny)
            if blocked and abs(self.tx - old_tx) < 1e-6 and abs(self.ty - old_ty) < 1e-6:
                # Use grass-biased direction — this naturally steers away from beach/water
                self.dx, self.dy = self._best_flee_direction(grid)
                self._refresh_facing()

    # ------------------------------------------------------------------
    # Draw
    # ------------------------------------------------------------------

    def draw(self, screen: pygame.Surface, cam_x: float, cam_y: float,
             tile_size: float):
        if self.dead_state == "fresh":
            key = "dead_left" if self.death_facing == "left" else "dead_right"
        elif self.dead_state == "decayed":
            key = "decayed_left" if self.death_facing == "left" else "decayed_right"
        elif self._lunge_timer > 0:
            key = "lunge_left" if self.facing == "left" else "lunge_right"
        elif self.state == Wolf.EAT:
            key = f"eat_{self.facing}"
        elif self.state == Wolf.IDLE:
            key = f"idle_{self.facing}"
        else:
            key = self.facing

        effective_ts = tile_size * self.size_scale
        sx_f = self.tx * tile_size - cam_x
        sy_f = self.ty * tile_size - cam_y
        sx   = round(sx_f)
        sy   = round(sy_f)

        if tile_size < Wolf.LOD_THRESHOLD:
            dot_r = max(1, round(effective_ts * 0.65))
            color = self._avg_colors.get(key, (170, 150, 120))
            pygame.draw.circle(screen, color, (sx, sy), dot_r)
            return

        sprite = self._scaled(key, effective_ts)
        w, h   = sprite.get_size()
        screen.blit(sprite, (round(sx_f - w / 2), round(sy_f - h / 2)))

        if self.dead_state is None and self.hp < self.max_hp:
            bar_w = w
            bar_h = max(2, round(effective_ts) // 7)
            bar_x = round(sx_f - w / 2)
            bar_y = round(sy_f - h / 2) - bar_h - 1
            pygame.draw.rect(screen, (60, 0, 0),    (bar_x, bar_y, bar_w, bar_h))
            filled = round(bar_w * max(0.0, self.hp / self.max_hp))
            pygame.draw.rect(screen, (200, 60, 40), (bar_x, bar_y, filled, bar_h))
