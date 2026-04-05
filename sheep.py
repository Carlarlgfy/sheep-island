import pygame
import math
import random
import os

from mapgen import WATER, GRASS, DIRT

_SHEEP_DIR = os.path.join(os.path.dirname(__file__), "sheep experiment")

# ---------------------------------------------------------------------------
# Hunger / eating  (+15% baseline hunger rate)
# ---------------------------------------------------------------------------
HUNGER_RATE              = 0.01610  # 15% higher than original 0.014
EAT_RATE                 = 0.22
EAT_DURATION             = 3.5
HUNGER_THRESHOLD         = 0.55
HUNGER_URGENCY_THRESHOLD = 0.65
STARVING_THRESHOLD       = 0.80
HUNGER_DEATH             = 1.0
REGROWTH_TIME            = 432.0   # 20% slower regrowth; more pressure on herds to migrate
EAT_AREA_RADIUS          = 2       # tiles around eater also consumed (strips a patch)
EAT_AREA_CHANCE          = 0.25    # probability each surrounding tile is eaten

# ---------------------------------------------------------------------------
# Death / corpse
# ---------------------------------------------------------------------------
DAY_DURATION             = 300.0   # sim-seconds per in-game day (matches main.py)
CORPSE_FRESH_MIN         = DAY_DURATION * 2    # 2 days as fresh corpse
CORPSE_FRESH_MAX         = DAY_DURATION * 3    # 3 days as fresh corpse
CORPSE_DECAYED_MIN       = DAY_DURATION * 2    # 2 days as decayed corpse
CORPSE_DECAYED_MAX       = DAY_DURATION * 3    # 3 days as decayed corpse
CORPSE_AVERSION_RADIUS   = 2.5     # tiles — living sheep try to stay this far from corpses
CORPSE_AVERSION_FORCE    = 2.0     # repulsion strength
FERTILIZE_RADIUS         = 3       # tile radius of fertilizer effect when corpse decays
FERTILIZE_REGROWTH       = 60.0    # fast regrowth time (seconds) on fertilized tiles

# ---------------------------------------------------------------------------
# Lifespan  (10–18 days with heritable variation)
# ---------------------------------------------------------------------------
LIFESPAN_MIN           = 3000.0   # 10 days
LIFESPAN_MAX           = 5400.0   # 18 days
GENETIC_LIFESPAN_RANGE = 0.10     # ±10% heritable modifier; offspring inherit parent value

# ---------------------------------------------------------------------------
# Maturation  (2–4 days with heritable variation)
# ---------------------------------------------------------------------------
MATURITY_AGE_BASE        = 900.0   # 3-day midpoint (range: 600–1200 s)
GENETIC_MATURITY_RANGE   = 0.33    # ±33% → 2–4 day phenotype range

# ---------------------------------------------------------------------------
# Herding / flocking  (tighter clustering — ~20% closer spacing)
# ---------------------------------------------------------------------------
HERD_COHESION_RADIUS = 18.0
SEPARATION_RADIUS    = 1.4
SEPARATION_FORCE     = 1.4
COHESION_WEIGHT      = 0.68
SAME_HERD_WEIGHT     = 1.2
INTER_HERD_RADIUS    = 18.0
INTER_HERD_REPULSION = 0.35
FOLLOW_RADIUS        = 9.0
FOLLOW_CHANCE        = 0.35

# Parent bond
PARENT_PULL_WEIGHT   = 0.65
PARENT_AGE_CUTOFF    = 600.0   # 2 days — parent bond fades linearly to zero

# ---------------------------------------------------------------------------
# Awareness
# ---------------------------------------------------------------------------
AWARENESS_RADIUS   = 28.0
MATE_SEARCH_RADIUS = 20.0

# ---------------------------------------------------------------------------
# Reproduction
# ---------------------------------------------------------------------------
REPRODUCE_RADIUS   = 10.0
REPRODUCE_HUNGER   = 0.25
REPRODUCE_COOLDOWN = 900.0   # 3 days between matings
BASE_LITTER        = 1

# Gestation  (2–3 days base, heritable, lengthened by poor nutrition)
GESTATION_BASE         = 750.0   # 2.5-day midpoint (range: 600–900 s via genetics)
GESTATION_PER_LAMB     = 150.0   # +0.5 day per additional lamb
GENETIC_GESTATION_RANGE = 0.20   # ±20% heritable modifier
# Hunger multiplier during gestation: 2.0 for 1 lamb, +0.8 per additional lamb
GESTATION_HUNGER_BASE  = 2.0
GESTATION_HUNGER_SCALE = 0.8

# ---------------------------------------------------------------------------
# Genetics
# ---------------------------------------------------------------------------
GENETIC_SIZE_RANGE = 0.15


class Sheep:
    IDLE = "idle"
    WALK = "walk"
    EAT  = "eat"

    SPEED_MIN = 3.5
    SPEED_MAX = 6.0

    _sprites_raw: dict | None = None
    _cache: dict = {}
    _avg_colors: dict = {}   # sprite key → (r, g, b) average sampled from 10×10 grid

    LOD_THRESHOLD = 6.0   # tile_size below which we switch to flat-color dot

    def __init__(self, tile_x: float, tile_y: float, age: float = None,
                 genetic_size: float = None,
                 genetic_maturity: float = None,
                 genetic_lifespan: float = None,
                 genetic_gestation: float = None):
        self.tx = float(tile_x)
        self.ty = float(tile_y)
        self.dx = 0.0
        self.dy = 0.0
        self.facing  = "front"
        self.state   = Sheep.IDLE
        self.timer   = 0.0
        self.hunger  = random.uniform(0.1, 0.45)
        self.speed   = random.uniform(Sheep.SPEED_MIN, Sheep.SPEED_MAX)

        # Genetic traits — all clamp to their allowed range
        def _clamp_genetic(val, r):
            return max(1.0 - r, min(1.0 + r, val))

        self.genetic_size = (_clamp_genetic(genetic_size, GENETIC_SIZE_RANGE)
                             if genetic_size is not None
                             else random.uniform(1.0 - GENETIC_SIZE_RANGE,
                                                 1.0 + GENETIC_SIZE_RANGE))

        self.genetic_maturity = (_clamp_genetic(genetic_maturity, GENETIC_MATURITY_RANGE)
                                 if genetic_maturity is not None
                                 else random.uniform(1.0 - GENETIC_MATURITY_RANGE,
                                                     1.0 + GENETIC_MATURITY_RANGE))

        self.genetic_lifespan = (_clamp_genetic(genetic_lifespan, GENETIC_LIFESPAN_RANGE)
                                 if genetic_lifespan is not None
                                 else random.uniform(1.0 - GENETIC_LIFESPAN_RANGE,
                                                     1.0 + GENETIC_LIFESPAN_RANGE))

        self.genetic_gestation = (_clamp_genetic(genetic_gestation, GENETIC_GESTATION_RANGE)
                                  if genetic_gestation is not None
                                  else random.uniform(1.0 - GENETIC_GESTATION_RANGE,
                                                      1.0 + GENETIC_GESTATION_RANGE))

        # Derive per-sheep timing from genetics
        self.maturity_age = MATURITY_AGE_BASE * self.genetic_maturity
        self.lifespan     = random.uniform(LIFESPAN_MIN, LIFESPAN_MAX) * self.genetic_lifespan

        self.age = float(age) if age is not None else random.uniform(
            self.maturity_age, self.maturity_age * 2.0)

        # Running average of hunger over the sheep's life (nutrition stress tracker)
        # Initialised to the starting hunger so stress is based on lived experience
        self._avg_hunger: float = self.hunger

        self.infertile = random.random() < 0.001
        self.genius    = random.random() < 0.001
        self.curiosity = random.uniform(0.0, 1.0)   # 0=homebodies, 1=wanderers

        # Herd membership (assigned by HerdManager)
        self.herd_id = -1

        # Parent reference — set at birth; None for initial placed sheep
        self.parent: "Sheep | None" = None

        # Herd influence — written by HerdManager every frame
        self.herd_cx           = self.tx   # herd center x (tile coords)
        self.herd_cy           = self.ty   # herd center y (tile coords)
        self.herd_pull_strength = 0.38     # cohesion weight toward center (was 0.3)
        self.migration_mode    = False     # herd is migrating as one
        self.migrate_tx        = self.tx  # migration target tile x
        self.migrate_ty        = self.ty  # migration target tile y

        # Gestation
        self.pregnant                = False
        self.gestation_timer         = 0.0
        self._pending_litter: list[tuple] = []   # (genetic_size, speed) per lamb
        self._gestation_hunger_mult  = 1.0   # set when pregnancy begins

        self.alive     = True
        self.fertility = random.uniform(0.3, 1.0)
        self.reproduce_cooldown = random.uniform(0, REPRODUCE_COOLDOWN * 0.5)

        # Death / corpse state
        # None = living; "fresh" = fresh corpse; "decayed" = decayed corpse
        self.dead_state   = None
        self.death_timer  = 0.0
        self.death_facing = "right"   # facing direction recorded at moment of death

        self._schedule_idle()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_adult(self) -> bool:
        # Chronic poor nutrition delays sexual maturity by up to 25%
        stress = max(0.0, self._avg_hunger - 0.4) / 0.6
        return self.age >= self.maturity_age * (1.0 + stress * 0.25)

    @property
    def is_living(self) -> bool:
        """True only for sheep that are alive and not yet a corpse."""
        return self.alive and self.dead_state is None

    @property
    def size_scale(self) -> float:
        growth = 0.5 + 0.5 * min(1.0, self.age / self.maturity_age)
        return growth * self.genetic_size

    @property
    def _reproduce_threshold(self) -> float:
        return REPRODUCE_HUNGER / self.genetic_size

    @property
    def _effective_lifespan(self) -> float:
        """Nutrition-stressed sheep age faster — chronic hunger reduces lifespan by up to 25%."""
        stress = self._avg_hunger * 0.25
        return self.lifespan * max(0.75, 1.0 - stress)

    # ------------------------------------------------------------------
    # Sprite loading and per-tile-size caching
    # ------------------------------------------------------------------

    @classmethod
    def load_sprites(cls):
        if cls._sprites_raw is not None:
            return
        front        = pygame.image.load(os.path.join(_SHEEP_DIR, "Front_Facing.png")).convert_alpha()
        behind       = pygame.image.load(os.path.join(_SHEEP_DIR, "Behind_Facing.png")).convert_alpha()
        right        = pygame.image.load(os.path.join(_SHEEP_DIR, "Right_Facing.png")).convert_alpha()
        left         = pygame.transform.flip(right, True, False)
        eat_front    = pygame.image.load(os.path.join(_SHEEP_DIR, "Eating_Grass_Forward_Facing.png")).convert_alpha()
        eat_right    = pygame.image.load(os.path.join(_SHEEP_DIR, "Facing_To_The_Right_Eating_Grass.png")).convert_alpha()
        eat_left     = pygame.transform.flip(eat_right, True, False)
        dead_right   = pygame.image.load(os.path.join(_SHEEP_DIR, "Dead_Sheep.png")).convert_alpha()
        dead_left    = pygame.transform.flip(dead_right, True, False)
        decayed_right = pygame.image.load(os.path.join(_SHEEP_DIR, "Decayed_Sheep_Corpse.png")).convert_alpha()
        decayed_left  = pygame.transform.flip(decayed_right, True, False)
        cls._sprites_raw = {
            "front":          front,
            "behind":         behind,
            "right":          right,
            "left":           left,
            "eat_front":      eat_front,
            "eat_behind":     eat_front,
            "eat_right":      eat_right,
            "eat_left":       eat_left,
            "dead_right":     dead_right,
            "dead_left":      dead_left,
            "decayed_right":  decayed_right,
            "decayed_left":   decayed_left,
        }
        cls._cache = {}
        cls._avg_colors = {k: cls._sample_avg_color(v) for k, v in cls._sprites_raw.items()}

    @classmethod
    def _sample_avg_color(cls, surf: pygame.Surface) -> tuple:
        """Sample a 10×10 grid of pixels, skip fully transparent ones, return (r,g,b)."""
        w, h = surf.get_size()
        xs = [int(w * (i + 0.5) / 10) for i in range(10)]
        ys = [int(h * (j + 0.5) / 10) for j in range(10)]
        r_sum = g_sum = b_sum = count = 0
        for x in xs:
            for y in ys:
                color = surf.get_at((x, y))
                if color.a > 32:   # skip mostly transparent pixels
                    r_sum += color.r
                    g_sum += color.g
                    b_sum += color.b
                    count += 1
        if count == 0:
            return (220, 220, 220)
        return (r_sum // count, g_sum // count, b_sum // count)

    @classmethod
    def _scaled(cls, key: str, tile_size: float) -> pygame.Surface:
        ts = max(1, round(tile_size))
        if ts not in cls._cache:
            target_h = max(4, ts * 2)
            entry = {}
            for k, surf in cls._sprites_raw.items():
                ow, oh = surf.get_size()
                nw = max(1, int(ow * target_h / oh))
                entry[k] = pygame.transform.scale(surf, (nw, target_h))
            cls._cache[ts] = entry
        return cls._cache[ts][key]

    # ------------------------------------------------------------------
    # State scheduling
    # ------------------------------------------------------------------

    def _schedule_idle(self):
        self.state = Sheep.IDLE
        self.dx    = 0.0
        self.dy    = 0.0
        self.timer = random.uniform(2.0, 5.0)

    def _schedule_eat(self):
        self.state = Sheep.EAT
        self.dx    = 0.0
        self.dy    = 0.0
        self.timer = EAT_DURATION

    def _schedule_walk(self, flock: list = None):
        self.state = Sheep.WALK
        self.timer = random.uniform(1.5, 4.0)

        # --- Migration mode: steer toward the herd's chosen grass target ---
        if self.migration_mode:
            dtx = self.migrate_tx - self.tx
            dty = self.migrate_ty - self.ty
            dist_to_target = math.sqrt(dtx * dtx + dty * dty)
            if dist_to_target > 1.5:
                noise   = random.gauss(0, 0.18)
                angle_m = math.atan2(dty, dtx) + noise
                self.dx = math.cos(angle_m)
                self.dy = math.sin(angle_m)
                self._refresh_facing()
            else:
                # Arrived at target — idle briefly
                self._schedule_idle()
            return

        # --- Base random direction ---
        angle = random.uniform(0, 2 * math.pi)
        bx    = math.cos(angle)
        by    = math.sin(angle)

        # Curious sheep weight random more; homebodies weight cohesion more
        rand_w = 0.25 + self.curiosity * 0.45

        # 1. Nearby flock cohesion (same herd) + inter-herd repulsion + corpse avoidance
        if flock:
            cohesion_w = max(0.10, COHESION_WEIGHT - self.curiosity * 0.25)
            same_hx, same_hy, same_count = 0.0, 0.0, 0
            repel_x,  repel_y,  rep_count = 0.0, 0.0, 0
            corpse_rx, corpse_ry, corpse_count = 0.0, 0.0, 0
            for other in flock:
                if other is self:
                    continue
                ddx  = other.tx - self.tx
                ddy  = other.ty - self.ty
                dist = math.sqrt(ddx * ddx + ddy * ddy)
                # Corpse avoidance — steer away from any corpse in range
                if other.dead_state is not None:
                    if 0 < dist < CORPSE_AVERSION_RADIUS * 2.5:
                        corpse_rx += (self.tx - other.tx) / dist
                        corpse_ry += (self.ty - other.ty) / dist
                        corpse_count += 1
                    continue
                same_herd = (self.herd_id != -1 and other.herd_id == self.herd_id)
                if same_herd and 0 < dist < HERD_COHESION_RADIUS:
                    same_hx += (ddx / dist) * SAME_HERD_WEIGHT
                    same_hy += (ddy / dist) * SAME_HERD_WEIGHT
                    same_count += 1
                elif not same_herd and 0 < dist < INTER_HERD_RADIUS:
                    # Repel away from other-herd sheep (creates bubble between herds)
                    repel_x -= ddx / dist
                    repel_y -= ddy / dist
                    rep_count += 1
            if same_count > 0:
                bx = bx * rand_w + (same_hx / same_count) * cohesion_w
                by = by * rand_w + (same_hy / same_count) * cohesion_w
            if rep_count > 0:
                bx += (repel_x / rep_count) * INTER_HERD_REPULSION
                by += (repel_y / rep_count) * INTER_HERD_REPULSION
            if corpse_count > 0:
                bx += (corpse_rx / corpse_count) * 1.8
                by += (corpse_ry / corpse_count) * 1.8

        # 2. Gravitational pull toward herd center of mass
        if self.herd_id >= 0:
            gcx = self.herd_cx - self.tx
            gcy = self.herd_cy - self.ty
            dist_c = math.sqrt(gcx * gcx + gcy * gcy)
            if dist_c > 0:
                # Pull scales with distance (far sheep pulled harder)
                # and is reduced by curiosity (wanderers resist the pull)
                pull = min(self.herd_pull_strength,
                           self.herd_pull_strength * dist_c / (HERD_COHESION_RADIUS * 0.6))
                pull *= (1.0 - self.curiosity * 0.50)
                bx += (gcx / dist_c) * pull
                by += (gcy / dist_c) * pull

        # 3. Parent pull (young sheep only)
        if (self.parent is not None and self.parent.alive
                and self.parent.dead_state is None
                and self.age < PARENT_AGE_CUTOFF):
            pdx = self.parent.tx - self.tx
            pdy = self.parent.ty - self.ty
            pdist = math.sqrt(pdx * pdx + pdy * pdy)
            if pdist > 0:
                fade = 1.0 - self.age / PARENT_AGE_CUTOFF
                pw   = PARENT_PULL_WEIGHT * fade
                bx  += (pdx / pdist) * pw
                by  += (pdy / pdist) * pw

        mag = math.sqrt(bx * bx + by * by)
        if mag > 0:
            self.dx = bx / mag
            self.dy = by / mag
        else:
            self.dx = math.cos(angle)
            self.dy = math.sin(angle)
        self._refresh_facing()

    def _refresh_facing(self):
        if abs(self.dx) >= abs(self.dy):
            self.facing = "right" if self.dx >= 0 else "left"
        else:
            self.facing = "front" if self.dy > 0 else "behind"

    # ------------------------------------------------------------------
    # Herd helpers
    # ------------------------------------------------------------------

    def _separation_delta(self, flock: list, dt: float):
        sx, sy = 0.0, 0.0
        for other in flock:
            if other is self:
                continue
            ddx  = self.tx - other.tx
            ddy  = self.ty - other.ty
            dist = math.sqrt(ddx * ddx + ddy * ddy)
            if other.dead_state is not None:
                # Corpse avoidance — stronger and wider than normal separation
                if 0 < dist < CORPSE_AVERSION_RADIUS:
                    strength = (CORPSE_AVERSION_RADIUS - dist) / CORPSE_AVERSION_RADIUS
                    sx += (ddx / dist) * strength * CORPSE_AVERSION_FORCE * dt
                    sy += (ddy / dist) * strength * CORPSE_AVERSION_FORCE * dt
            elif 0 < dist < SEPARATION_RADIUS:
                strength = (SEPARATION_RADIUS - dist) / SEPARATION_RADIUS
                sx += (ddx / dist) * strength * SEPARATION_FORCE * dt
                sy += (ddy / dist) * strength * SEPARATION_FORCE * dt
        return sx, sy

    def _try_follow(self, flock: list) -> bool:
        for other in flock:
            if other is self or other.dead_state is not None or other.state != Sheep.WALK:
                continue
            ddx  = other.tx - self.tx
            ddy  = other.ty - self.ty
            dist = math.sqrt(ddx * ddx + ddy * ddy)
            if dist >= FOLLOW_RADIUS:
                continue
            same_herd = (self.herd_id != -1 and other.herd_id == self.herd_id)
            # Same-herd: normal follow chance; other-herd: only curious sheep follow
            chance = FOLLOW_CHANCE if same_herd else FOLLOW_CHANCE * self.curiosity * 0.4
            if random.random() < chance:
                self.state = Sheep.WALK
                self.timer = random.uniform(1.0, 3.0)
                noise = random.uniform(-0.3, 0.3)
                angle = math.atan2(other.dy, other.dx) + noise
                self.dx = math.cos(angle)
                self.dy = math.sin(angle)
                self._refresh_facing()
                return True
        return False

    def _find_nearest_grass(self, grid: list, rows: int, cols: int):
        scan_r = int(AWARENESS_RADIUS)
        tx, ty = int(self.tx), int(self.ty)
        best_dist_sq = scan_r * scan_r + 1
        best_dc, best_dr = 0, 0
        found = False

        for dr in range(-scan_r, scan_r + 1):
            r = ty + dr
            if not (0 <= r < rows):
                continue
            for dc in range(-scan_r, scan_r + 1):
                dist_sq = dr * dr + dc * dc
                if dist_sq > scan_r * scan_r:
                    continue
                c = tx + dc
                if not (0 <= c < cols):
                    continue
                if grid[r][c] == GRASS and dist_sq < best_dist_sq:
                    best_dist_sq = dist_sq
                    best_dc, best_dr = dc, dr
                    found = True

        if found and best_dist_sq > 0:
            dist = math.sqrt(best_dist_sq)
            return best_dc / dist, best_dr / dist
        return None

    def _find_nearest_mate(self, flock: list):
        best_dist = float('inf')
        best_dx, best_dy = 0.0, 0.0
        found = False

        for other in flock:
            if other is self or other.dead_state is not None or not other.is_adult or other.infertile or other.pregnant:
                continue
            if other.hunger >= other._reproduce_threshold or other.reproduce_cooldown > 0:
                continue
            ddx  = other.tx - self.tx
            ddy  = other.ty - self.ty
            dist = math.sqrt(ddx * ddx + ddy * ddy)
            if dist < best_dist and dist <= MATE_SEARCH_RADIUS:
                best_dist = dist
                if dist > 0:
                    best_dx = ddx / dist
                    best_dy = ddy / dist
                found = True

        return (best_dx, best_dy) if found else None

    # ------------------------------------------------------------------
    # Reproduction — now sets pregnancy instead of immediate birth
    # ------------------------------------------------------------------

    def _try_reproduce(self, flock: list, grid: list, new_sheep: list):
        if self.infertile or self.pregnant:
            return
        for other in flock:
            if other is self or other.dead_state is not None or not other.is_adult or other.infertile or other.pregnant:
                continue
            if other.hunger >= other._reproduce_threshold or other.reproduce_cooldown > 0:
                continue
            ddx  = other.tx - self.tx
            ddy  = other.ty - self.ty
            dist = math.sqrt(ddx * ddx + ddy * ddy)
            if dist > REPRODUCE_RADIUS:
                continue

            # Litter size 1-4; higher fertility = more lambs (rare)
            litter_count = BASE_LITTER
            if self.fertility > 0.7 and random.random() < 0.30:
                litter_count += 1
            if litter_count >= 2 and self.fertility > 0.85 and random.random() < 0.20:
                litter_count += 1
            if litter_count >= 3 and self.fertility > 0.95 and random.random() < 0.10:
                litter_count += 1

            def _inherit(parent_a, parent_b, trait, r):
                mid = (getattr(parent_a, trait) + getattr(parent_b, trait)) / 2.0
                return max(1.0 - r, min(1.0 + r, mid + random.gauss(0, r * 0.12)))

            pending = []
            for _ in range(litter_count):
                parent_size = (self.genetic_size + other.genetic_size) / 2.0
                baby_size   = max(1.0 - GENETIC_SIZE_RANGE,
                                  min(1.0 + GENETIC_SIZE_RANGE,
                                      parent_size + random.gauss(0, 0.03)))
                mid_speed   = (self.speed + other.speed) / 2.0
                baby_speed  = max(Sheep.SPEED_MIN * 0.7,
                                  min(Sheep.SPEED_MAX * 1.3,
                                      mid_speed + random.gauss(0, 0.15)))
                baby_maturity  = _inherit(self, other, "genetic_maturity",  GENETIC_MATURITY_RANGE)
                baby_lifespan  = _inherit(self, other, "genetic_lifespan",  GENETIC_LIFESPAN_RANGE)
                baby_gestation = _inherit(self, other, "genetic_gestation", GENETIC_GESTATION_RANGE)
                pending.append((baby_size, baby_speed, baby_maturity, baby_lifespan, baby_gestation))

            # Gestation time: genetic base × nutrition stress of the mother
            # Poor nutrition extends pregnancy (fewer resources for foetal development)
            nutrition_delay = max(0.0, self._avg_hunger - 0.3) * 0.5
            base_gestation  = (GESTATION_BASE * self.genetic_gestation
                               + GESTATION_PER_LAMB * (litter_count - 1))
            self.pregnant               = True
            self.gestation_timer        = base_gestation * (1.0 + nutrition_delay)
            self._gestation_hunger_mult = GESTATION_HUNGER_BASE + GESTATION_HUNGER_SCALE * (litter_count - 1)
            self._pending_litter        = pending

            self.reproduce_cooldown  = REPRODUCE_COOLDOWN
            other.reproduce_cooldown = REPRODUCE_COOLDOWN
            return

    def _birth(self, grid: list, new_sheep: list):
        """Spawn pending offspring when gestation completes."""
        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        for baby_size, baby_speed, baby_maturity, baby_lifespan, baby_gestation in self._pending_litter:
            attempts = 0
            while attempts < 8:
                attempts += 1
                ox = self.tx + random.uniform(-2.0, 2.0)
                oy = self.ty + random.uniform(-2.0, 2.0)
                c, r = int(ox), int(oy)
                if 0 <= r < rows and 0 <= c < cols and grid[r][c] != WATER:
                    baby = Sheep(ox, oy, age=0.0,
                                 genetic_size=baby_size,
                                 genetic_maturity=baby_maturity,
                                 genetic_lifespan=baby_lifespan,
                                 genetic_gestation=baby_gestation)
                    baby.hunger   = 0.0
                    baby.speed    = baby_speed
                    baby.herd_id  = self.herd_id
                    baby.parent   = self
                    new_sheep.append(baby)
                    break

    # ------------------------------------------------------------------
    # Death helpers
    # ------------------------------------------------------------------

    def _die(self):
        """Transition a living sheep into the fresh-corpse state."""
        self.dead_state   = "fresh"
        self.death_timer  = random.uniform(CORPSE_FRESH_MIN, CORPSE_FRESH_MAX)
        self.death_facing = self.facing
        # Stop movement — corpse is inert
        self.state = Sheep.IDLE
        self.dx    = 0.0
        self.dy    = 0.0
        # Remove from active herd
        self.herd_id        = -1
        self.pregnant       = False
        self._pending_litter = []

    def _update_corpse(self, dt: float, grid: list, regrowth_timers: dict,
                       dirty_callback=None):
        """Tick the corpse state machine."""
        self.death_timer -= dt
        if self.death_timer > 0:
            return

        if self.dead_state == "fresh":
            # Transition to decayed — apply fertilizer boost first
            self.dead_state  = "decayed"
            self.death_timer = random.uniform(CORPSE_DECAYED_MIN, CORPSE_DECAYED_MAX)
            self._apply_fertilizer(grid, regrowth_timers, dirty_callback)
        elif self.dead_state == "decayed":
            # Fully gone — remove from simulation
            self.alive = False

    def _apply_fertilizer(self, grid: list, regrowth_timers: dict,
                          dirty_callback=None):
        """Boost grass regrowth on nearby dirt tiles when the corpse starts decaying."""
        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        cx, cy = int(self.tx), int(self.ty)
        r = FERTILIZE_RADIUS
        for dr in range(-r, r + 1):
            for dc in range(-r, r + 1):
                if math.sqrt(dr * dr + dc * dc) > r:
                    continue
                tr, tc = cy + dr, cx + dc
                if not (0 <= tr < rows and 0 <= tc < cols):
                    continue
                if grid[tr][tc] == DIRT:
                    # Reduce regrowth timer to fast value if not already faster
                    current = regrowth_timers.get((tr, tc), REGROWTH_TIME)
                    regrowth_timers[(tr, tc)] = min(current, FERTILIZE_REGROWTH)

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(self, dt: float, grid: list, regrowth_timers: dict,
               flock: list, new_sheep: list, dirty_callback=None):
        if not self.alive:
            return

        # --- Corpse state: skip all normal logic ---
        if self.dead_state is not None:
            self._update_corpse(dt, grid, regrowth_timers, dirty_callback)
            return

        self.age   = self.age + dt
        self.timer -= dt
        if self.reproduce_cooldown > 0:
            self.reproduce_cooldown = max(0.0, self.reproduce_cooldown - dt)

        # Gestation tick — birth when timer expires
        if self.pregnant:
            self.gestation_timer -= dt
            if self.gestation_timer <= 0:
                self._birth(grid, new_sheep)
                self.pregnant        = False
                self._pending_litter = []

        # Hunger — pregnant sheep get hungry faster (multiplier scales with litter size)
        hunger_mult = self._gestation_hunger_mult if self.pregnant else 1.0
        self.hunger = min(1.0, self.hunger + HUNGER_RATE * self.genetic_size * hunger_mult * dt)

        # Nutrition stress: exponential moving average over ~1 sim-day window
        alpha = dt / DAY_DURATION
        self._avg_hunger += (self.hunger - self._avg_hunger) * min(1.0, alpha)

        # --- Death checks ---
        if self.age >= self._effective_lifespan or self.hunger >= HUNGER_DEATH:
            self._die()
            return

        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        col  = int(self.tx)
        row  = int(self.ty)
        on_map   = 0 <= row < rows and 0 <= col < cols
        on_grass = on_map and grid[row][col] == GRASS

        urgency = 0.0
        if self.hunger >= HUNGER_URGENCY_THRESHOLD:
            urgency = (self.hunger - HUNGER_URGENCY_THRESHOLD) / (1.0 - HUNGER_URGENCY_THRESHOLD)

        starving = self.hunger >= STARVING_THRESHOLD

        # --- EAT ---
        if self.state == Sheep.EAT:
            self.hunger = max(0.0, self.hunger - EAT_RATE * dt)
            if self.timer <= 0 or self.hunger <= 0.1:
                self._schedule_idle()
            return

        # Start eating immediately if standing on grass and hungry
        if self.hunger >= HUNGER_THRESHOLD and on_grass:
            grid[row][col] = DIRT
            regrowth_timers[(row, col)] = REGROWTH_TIME
            if dirty_callback:
                dirty_callback(row, col)
            # Strip surrounding tiles — sheep graze an area, not just one tile
            for dr in range(-EAT_AREA_RADIUS, EAT_AREA_RADIUS + 1):
                for dc in range(-EAT_AREA_RADIUS, EAT_AREA_RADIUS + 1):
                    if dr == 0 and dc == 0:
                        continue
                    nr, nc = row + dr, col + dc
                    if (0 <= nr < rows and 0 <= nc < cols
                            and grid[nr][nc] == GRASS
                            and random.random() < EAT_AREA_CHANCE):
                        grid[nr][nc] = DIRT
                        regrowth_timers[(nr, nc)] = REGROWTH_TIME
                        if dirty_callback:
                            dirty_callback(nr, nc)
            self._schedule_eat()
            return

        # --- State transitions ---

        if starving:
            if self.state == Sheep.IDLE or self.timer <= 0:
                direction = self._find_nearest_grass(grid, rows, cols)
                if direction:
                    self.state = Sheep.WALK
                    self.dx, self.dy = direction
                    self.timer = 1.5
                    self._refresh_facing()
                else:
                    self._schedule_walk(flock)

        elif self.state == Sheep.IDLE:
            if urgency > 0 and self.timer > 0.4:
                self.timer = min(self.timer, max(0.4, 1.0 - urgency * 0.7))

            if self.timer <= 0:
                # Migration override — herd is moving; join the march unless starving
                if self.migration_mode and not starving:
                    self._schedule_walk(flock)
                    return

                # Pregnant sheep prioritise finding food and staying near grass
                if self.pregnant:
                    if self.hunger >= HUNGER_THRESHOLD * 0.6:
                        direction = self._find_nearest_grass(grid, rows, cols)
                        if direction:
                            self.state = Sheep.WALK
                            self.dx, self.dy = direction
                            self.timer = 2.0
                            self._refresh_facing()
                            return
                    # Pregnant sheep idle longer (less aimless wandering)
                    self._schedule_idle()
                    self.timer *= 1.4
                    return

                ready_to_mate = (self.is_adult
                                 and not self.infertile
                                 and not self.pregnant
                                 and self.hunger < self._reproduce_threshold
                                 and self.reproduce_cooldown <= 0)
                if ready_to_mate:
                    self._try_reproduce(flock, grid, new_sheep)
                    if self.reproduce_cooldown <= 0 and not self.pregnant:
                        mate_dir = self._find_nearest_mate(flock)
                        if mate_dir:
                            self.state = Sheep.WALK
                            self.dx, self.dy = mate_dir
                            self.timer = 2.0
                            self._refresh_facing()
                            return

                if not self._try_follow(flock):
                    if self.hunger >= HUNGER_THRESHOLD * 0.7:
                        direction = self._find_nearest_grass(grid, rows, cols)
                        if direction:
                            self.state = Sheep.WALK
                            self.dx, self.dy = direction
                            self.timer = 2.0
                            self._refresh_facing()
                        else:
                            self._schedule_walk(flock)
                    else:
                        self._schedule_walk(flock)

        elif self.state == Sheep.WALK and self.timer <= 0:
            self._schedule_idle()

        # --- Movement ---
        if self.state == Sheep.WALK:
            sx, sy = self._separation_delta(flock, dt)
            speed_mult = 1.0 + urgency * 1.8
            new_tx = self.tx + self.dx * self.speed * speed_mult * dt + sx
            new_ty = self.ty + self.dy * self.speed * speed_mult * dt + sy

            ncol = int(new_tx)
            nrow = int(new_ty)
            if 0 <= nrow < rows and 0 <= ncol < cols and grid[nrow][ncol] != WATER:
                self.tx = new_tx
                self.ty = new_ty
            else:
                self.state = Sheep.IDLE
                self.dx    = 0.0
                self.dy    = 0.0
                self.timer = random.uniform(0.3, 1.0)

    # ------------------------------------------------------------------
    # Draw
    # ------------------------------------------------------------------

    def draw(self, screen: pygame.Surface, cam_x: float, cam_y: float, tile_size: float):
        # Choose sprite key
        if self.dead_state == "fresh":
            key = "dead_left" if self.death_facing == "left" else "dead_right"
        elif self.dead_state == "decayed":
            key = "decayed_left" if self.death_facing == "left" else "decayed_right"
        else:
            key = f"eat_{self.facing}" if self.state == Sheep.EAT else self.facing

        effective_ts = tile_size * self.size_scale
        ts           = max(1, round(tile_size))
        sx_center    = int(self.tx * ts - cam_x)
        sy_center    = int(self.ty * ts - cam_y)

        # --- LOD: flat colored dot when zoomed far out ---
        if tile_size < Sheep.LOD_THRESHOLD:
            dot_r = max(1, round(effective_ts * 0.6))
            color = self._avg_colors.get(key, (220, 220, 220))
            pygame.draw.circle(screen, color, (sx_center, sy_center), dot_r)
            return

        sprite = self._scaled(key, effective_ts)
        w, h   = sprite.get_size()
        sx     = sx_center - w // 2
        sy     = sy_center - h // 2
        screen.blit(sprite, (sx, sy))

        # Hunger bar — only shown on living sheep
        if self.dead_state is None and self.hunger > 0.2:
            bar_w  = w
            bar_h  = max(2, round(effective_ts) // 7)
            bar_y  = sy - bar_h - 2
            filled = int(bar_w * self.hunger)
            pygame.draw.rect(screen, (40, 40, 40), (sx, bar_y, bar_w, bar_h))
            if self.hunger < 0.5:
                r = int(self.hunger * 2 * 255)
                g = 200
            else:
                r = 220
                g = int((1.0 - self.hunger) * 2 * 200)
            pygame.draw.rect(screen, (r, g, 30), (sx, bar_y, filled, bar_h))
