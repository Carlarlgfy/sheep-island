import pygame
import math
import random
import os

from mapgen import WATER, GRASS, DIRT, SNOW, is_walkable_tile, advance_until_blocked

_SHEEP_DIR = os.path.join(os.path.dirname(__file__), "white sheep")

# ---------------------------------------------------------------------------
# Hunger / eating  (+15% baseline hunger rate)
# ---------------------------------------------------------------------------
HUNGER_RATE              = 0.0045   # slow accumulation — sheep graze peacefully
EAT_RATE                 = 0.10
EAT_DURATION             = 20.0   # sheep graze slowly — long eating sessions
HUNGER_THRESHOLD         = 0.55
HUNGER_URGENCY_THRESHOLD = 0.65
STARVING_THRESHOLD       = 0.80
REGROWTH_TIME            = 750.0   # ~2.5 days before eaten grass is eligible to regrow

# ---------------------------------------------------------------------------
# HP
# ---------------------------------------------------------------------------
HP_MIN           = 10
HP_MAX           = 25
# HP drain per sim-second at full hunger (linear from 0 at hunger=0.5 to this at hunger=1.0)
HP_DRAIN_RATE    = 1.0 / 30.0
# HP restored per sim-second while actively eating
HP_EAT_REGEN     = 0.3

# ---------------------------------------------------------------------------
# Death / corpse
# ---------------------------------------------------------------------------
DAY_DURATION             = 300.0   # sim-seconds per in-game day (matches main.py)

# ---------------------------------------------------------------------------
# Snow exposure damage
# ---------------------------------------------------------------------------
SNOW_EXPOSURE_THRESHOLD  = 300.0   # 1 full day in snow before damage begins
SNOW_DAMAGE_RATE         = 0.3     # HP/sec lost once threshold is exceeded
CORPSE_FRESH_MIN         = DAY_DURATION * 1    # corpses should turn quickly once dead
CORPSE_FRESH_MAX         = DAY_DURATION * 2
CORPSE_DECAYED_MIN       = DAY_DURATION * 1
CORPSE_DECAYED_MAX       = DAY_DURATION * 2
CORPSE_AVERSION_RADIUS   = 2.5     # tiles — living sheep try to stay this far from corpses
CORPSE_AVERSION_FORCE    = 2.0     # repulsion strength
FERTILIZE_RADIUS         = 3       # tile radius of fertilizer effect when corpse decays
FERTILIZE_REGROWTH       = 60.0    # fast regrowth time (seconds) on fertilized tiles

# Wolf / predator
MEAT_PER_SIZE_UNIT       = 0.95    # corpse meat scales with body size and HP
WOLF_FEAR_WEIGHT         = 14.0    # how strongly wolf flee direction overrides normal walk
FLEE_HERD_WEIGHT         = 1.4
FLEE_GRASS_WEIGHT        = 0.45
PROTECTOR_PULL_WEIGHT    = 2.1
GRASS_PULL_WEIGHT        = 1.2
OFF_GRASS_PULL_WEIGHT    = 2.6

# ---------------------------------------------------------------------------
# Lifespan  (13–21 days with heritable variation; +3 days average vs. old 10–18)
# ---------------------------------------------------------------------------
LIFESPAN_MIN           = 3900.0   # 13 days
LIFESPAN_MAX           = 6300.0   # 21 days
GENETIC_LIFESPAN_RANGE = 0.10     # ±10% heritable modifier; offspring inherit parent value

# ---------------------------------------------------------------------------
# Maturation  (2–4 days with heritable variation)
# ---------------------------------------------------------------------------
MATURITY_AGE_BASE        = 900.0   # 3-day midpoint (range: 600–1200 s)
GENETIC_MATURITY_RANGE   = 0.33    # ±33% → 2–4 day phenotype range

# ---------------------------------------------------------------------------
# Herding / flocking  (very tight clustering — sheep stay close together)
# ---------------------------------------------------------------------------
HERD_COHESION_RADIUS = 5.0
SEPARATION_RADIUS    = 1.2
SEPARATION_FORCE     = 1.4
COHESION_WEIGHT      = 3.5
SAME_HERD_WEIGHT     = 8.0
INTER_HERD_RADIUS    = 10.0
INTER_HERD_REPULSION = 0.35
FOLLOW_RADIUS        = 6.0
FOLLOW_CHANCE        = 0.50

# ---------------------------------------------------------------------------
# Wanderer drift  (isolated sheep very slowly seek other isolated sheep)
# ---------------------------------------------------------------------------
WANDERER_ATTRACT_RADIUS = 80.0   # tile radius within which a wanderer notices others
WANDERER_DRIFT_WEIGHT   = 0.08   # very weak directional bias — barely perceptible

# Parent bond
PARENT_PULL_WEIGHT   = 0.65
PARENT_AGE_CUTOFF    = 600.0   # 2 days — parent bond fades linearly to zero

# ---------------------------------------------------------------------------
# Awareness
# ---------------------------------------------------------------------------
AWARENESS_RADIUS   = 28.0
MATE_SEARCH_RADIUS = 20.0
PROTECTOR_SEARCH_RADIUS = 36.0

# ---------------------------------------------------------------------------
# Reproduction
# ---------------------------------------------------------------------------
REPRODUCE_RADIUS   = 10.0
REPRODUCE_HUNGER   = 0.35    # raised from 0.25 so sheep can mate even when somewhat hungry
REPRODUCE_COOLDOWN = 600.0   # 2 days between matings (was 3)
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
GENETIC_SIZE_RANGE     = 0.15
GENETIC_STRENGTH_RANGE = 0.20   # heritable combat / body strength (dormant for ewes)
MALE_BIRTH_CHANCE      = 0.25   # fraction of offspring that are male

# ---------------------------------------------------------------------------
# Offspring factory — overridden by ram.py to enable Ram births
# ---------------------------------------------------------------------------
_OFFSPRING_FACTORY = None   # callable(ox, oy, sex, **kwargs) -> Sheep | Ram

# Social attraction — 1 (lone wolf) to 10 (inseparable herd-bound).
# Controls cohesion pull strength, boundary return, and grazing patch radius.
SOCIAL_NORM          = 7.5   # normaliser — a sheep with this value behaves like the old baseline
GRAZE_PATCH_R_BASE   = 10    # tile radius at social=1; shrinks toward 5 at social=10
WALK_COHESION_BOOST  = 0.015 # continuous herd-pull per tile of excess distance per second


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
                 genetic_gestation: float = None,
                 genetic_hp: int = None,
                 genetic_social: int = None,
                 genetic_strength: float = None):
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

        # HP — genetic integer (HP_MIN..HP_MAX); more HP = more hunger per tick
        self.genetic_hp = (int(max(HP_MIN, min(HP_MAX, genetic_hp)))
                           if genetic_hp is not None
                           else random.randint(HP_MIN, HP_MAX))
        self.hp = float(self.genetic_hp)

        # Social attraction — integer 1–10; heritable.
        # High values → stronger pull toward herd, tighter grazing patch.
        # Population mean ~5.5, std ~2.  Most sheep are moderately social.
        self.genetic_social = (int(max(1, min(10, genetic_social)))
                               if genetic_social is not None
                               else max(1, min(10, round(random.gauss(7.5, 1.5)))))

        # Strength — heritable; used by rams in combat, dormant for ewes.
        self.genetic_strength = (_clamp_genetic(genetic_strength, GENETIC_STRENGTH_RANGE)
                                 if genetic_strength is not None
                                 else random.uniform(1.0 - GENETIC_STRENGTH_RANGE,
                                                     1.0 + GENETIC_STRENGTH_RANGE))

        # Sex — "female" for base Sheep; overridden to "male" by Ram.
        self.sex = "female"

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
        # 97% homebodies, ~3% true wanderers — keeps herds intact
        if random.random() < 0.03:
            self.curiosity = random.uniform(0.7, 1.0)
        else:
            self.curiosity = random.uniform(0.0, 0.06)

        # Herd membership (assigned by HerdManager)
        self.herd_id = -1

        # Parent reference — set at birth; None for initial placed sheep
        self.parent: "Sheep | None" = None
        self.mate_partner: "Sheep | None" = None

        # Herd influence — written by HerdManager every frame
        self.herd_cx            = self.tx   # herd center of mass x (tile coords)
        self.herd_cy            = self.ty   # herd center of mass y (tile coords)
        self.herd_graze_cx      = self.tx   # shared grazing patch center x
        self.herd_graze_cy      = self.ty   # shared grazing patch center y
        self.herd_awareness_r   = 20.0     # herd awareness radius (written by HerdManager)
        self.herd_pull_strength = 0.38
        self.migration_mode     = False     # herd is migrating as one
        self.migrate_tx         = self.tx   # migration target tile x
        self.migrate_ty         = self.ty   # migration target tile y

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
        self.meat_value   = 0.0       # set to genetic_size * MEAT_PER_SIZE_UNIT on death
        self.max_meat_value = 0.0
        self.corpse_decay_rate = 1.0
        self._corpse_slot_count = 4

        # Wolf fear — written by Wolf.update() when a wolf is hunting nearby
        self.wolf_aware       = False
        self.wolf_flee_dx     = 0.0
        self.wolf_flee_dy     = 0.0
        self._wolf_fear_timer = 0.0

        # Snow exposure — accumulates while on snow; resets when leaving snow
        self.snow_exposure    = 0.0

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
    def age_decline_frac(self) -> float:
        start = self.lifespan * 0.50
        if self.age <= start:
            return 0.0
        return min(1.0, (self.age - start) / max(1.0, self.lifespan - start))

    @property
    def max_hp(self) -> float:
        return float(self.genetic_hp) * (1.0 - self.age_decline_frac * 0.35)

    @property
    def move_speed(self) -> float:
        return self.speed * (1.0 - self.age_decline_frac * 0.28)

    @property
    def _reproduce_threshold(self) -> float:
        return REPRODUCE_HUNGER / self.genetic_size

    @property
    def _effective_lifespan(self) -> float:
        """Nutrition-stressed sheep age faster — chronic hunger reduces lifespan by up to 25%."""
        stress = self._avg_hunger * 0.25
        return self.lifespan * max(0.75, 1.0 - stress)

    @property
    def _hunger_rate_mult(self) -> float:
        """Multiplier on base hunger accumulation rate.  Ram overrides to 0.95."""
        return 1.0

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
                self._schedule_idle()
            return

        # --- Wolf flee: overrides all other movement when scared ---
        if self.wolf_aware and (self.wolf_flee_dx != 0.0 or self.wolf_flee_dy != 0.0):
            fx = self.wolf_flee_dx
            fy = self.wolf_flee_dy
            if self.herd_id >= 0:
                hdx = self.herd_cx - self.tx
                hdy = self.herd_cy - self.ty
                hdist = math.hypot(hdx, hdy)
                if hdist > 0.25:
                    fx += (hdx / hdist) * 0.25
                    fy += (hdy / hdist) * 0.25
            mag = math.hypot(fx, fy)
            if mag > 0.0:
                self.dx = fx / mag
                self.dy = fy / mag
            else:
                self.dx = self.wolf_flee_dx
                self.dy = self.wolf_flee_dy
            self._refresh_facing()
            self.state = Sheep.WALK
            self.timer = random.uniform(3.4, 5.4)
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

        # 2. Gravitational pull toward herd center — scales with distance and social trait
        if self.herd_id >= 0:
            gcx = self.herd_cx - self.tx
            gcy = self.herd_cy - self.ty
            dist_c = math.sqrt(gcx * gcx + gcy * gcy)
            if dist_c > 0:
                # Social multiplier: genetic_social 1–10 → 0.18–1.82× baseline
                social_mult = self.genetic_social / SOCIAL_NORM
                dist_factor = dist_c / max(1.0, HERD_COHESION_RADIUS)
                pull = self.herd_pull_strength * dist_factor * social_mult

                # Hard boundary enforcement beyond 90% of awareness radius
                if dist_c > self.herd_awareness_r * 0.9:
                    soft_bound  = self.herd_awareness_r * 0.9
                    excess_frac = (dist_c - soft_bound) / max(1.0, self.herd_awareness_r * 0.2)
                    boundary_scale = max(0.15, 1.0 - self.curiosity * 0.85)
                    # High social sheep feel a stronger boundary push back
                    pull += 90.0 * min(excess_frac, 5.0) * boundary_scale * social_mult

                pull *= (1.0 - self.curiosity * 0.45)
                hunger_resist = max(0.0,
                    (self.hunger - HUNGER_THRESHOLD) / (1.0 - HUNGER_THRESHOLD))
                pull *= (1.0 - hunger_resist * 0.20)
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

        # 4. Wanderer drift: isolated sheep very slowly attract each other
        if self.herd_id == -1 and self.curiosity > 0.5 and flock:
            nearest_dist = WANDERER_ATTRACT_RADIUS * WANDERER_ATTRACT_RADIUS
            wdx, wdy = 0.0, 0.0
            for other in flock:
                if other is self or other.herd_id != -1 or other.dead_state is not None:
                    continue
                ddx = other.tx - self.tx
                ddy = other.ty - self.ty
                d_sq = ddx * ddx + ddy * ddy
                if d_sq < nearest_dist and d_sq > 0:
                    nearest_dist = d_sq
                    d = math.sqrt(d_sq)
                    wdx = ddx / d
                    wdy = ddy / d
            if nearest_dist < WANDERER_ATTRACT_RADIUS * WANDERER_ATTRACT_RADIUS:
                bx += wdx * WANDERER_DRIFT_WEIGHT
                by += wdy * WANDERER_DRIFT_WEIGHT

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

    # Radius within which bystanders avoid an active ram fight (written by ram.py)
    _FIGHT_EXCLUSION_RADIUS = 5.0

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
                continue
            # Fight exclusion — bystanders drift ≥5 tiles from active fighters.
            # A stray that wanders inside 1.5 tiles of a fighter may take minor damage.
            if getattr(other, 'ram_state', None) == 'fighting':
                excl_r = Sheep._FIGHT_EXCLUSION_RADIUS
                if 0 < dist < excl_r:
                    strength = (excl_r - dist) / excl_r
                    sx += (ddx / dist) * strength * 3.0 * dt
                    sy += (ddy / dist) * strength * 3.0 * dt
                    # Accidental contact damage (very close)
                    if dist < 1.5 and self.dead_state is None:
                        self.hp = max(0.0, self.hp - 0.5 * dt)
                continue
            if 0 < dist < SEPARATION_RADIUS:
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

    def _find_herd_grass(self, grid: list, rows: int, cols: int):
        """Two-stage grass search for herd members.

        Stage 1 — tight patch around the herd's shared grazing center.
          Radius shrinks with genetic_social (more social → tighter patch → less fan-out).
          This makes herd-mates graze near each other instead of spreading across the map.

        Stage 2 — wider scan around herd center (fallback when the local patch is stripped).
          Returns None if no grass anywhere in herd territory, triggering migration.

        Solo sheep fall back to _find_nearest_grass.
        """
        if self.herd_id < 0:
            return self._find_nearest_grass(grid, rows, cols)

        # --- Stage 1: shared grazing patch ---
        # Radius: social=1 → 10 tiles, social=10 → 5 tiles
        patch_r = max(5, GRAZE_PATCH_R_BASE - self.genetic_social // 2)
        gcx = int(self.herd_graze_cx)
        gcy = int(self.herd_graze_cy)

        best_sq = float('inf')
        best_dx, best_dy = 0.0, 0.0
        found = False
        r_sq = patch_r * patch_r

        for dr in range(-patch_r, patch_r + 1):
            r = gcy + dr
            if not (0 <= r < rows):
                continue
            for dc in range(-patch_r, patch_r + 1):
                if dr * dr + dc * dc > r_sq:
                    continue
                c = gcx + dc
                if not (0 <= c < cols):
                    continue
                if grid[r][c] != GRASS:
                    continue
                dtx = c + 0.5 - self.tx
                dty = r + 0.5 - self.ty
                d_sq = dtx * dtx + dty * dty
                if d_sq < best_sq:
                    best_sq = d_sq
                    found = True
                    d = math.sqrt(d_sq) if d_sq > 0 else 1.0
                    best_dx = dtx / d
                    best_dy = dty / d

        if found and best_sq > 0.25:
            return best_dx, best_dy

        # --- Stage 2: wider scan around herd center of mass ---
        hcx = int(self.herd_cx)
        hcy = int(self.herd_cy)
        scan_r = max(12, int(self.herd_awareness_r))

        best_sq = float('inf')
        found = False
        r_sq = scan_r * scan_r

        for dr in range(-scan_r, scan_r + 1):
            r = hcy + dr
            if not (0 <= r < rows):
                continue
            for dc in range(-scan_r, scan_r + 1):
                if dr * dr + dc * dc > r_sq:
                    continue
                c = hcx + dc
                if not (0 <= c < cols):
                    continue
                if grid[r][c] != GRASS:
                    continue
                dtx = c + 0.5 - self.tx
                dty = r + 0.5 - self.ty
                d_sq = dtx * dtx + dty * dty
                if d_sq < best_sq:
                    best_sq = d_sq
                    found = True
                    d = math.sqrt(d_sq) if d_sq > 0 else 1.0
                    best_dx = dtx / d
                    best_dy = dty / d

        if found and best_sq > 0.25:
            return best_dx, best_dy
        # No grass in herd territory — return None so migration can handle it
        return None

    def _find_nearest_mate(self, flock: list):
        best_dist = float('inf')
        best_dx, best_dy = 0.0, 0.0
        found = False

        for other in flock:
            if other is self or other.dead_state is not None or not other.is_adult or other.infertile or other.pregnant:
                continue
            # Require opposite sex (same sex = no pairing; unknown sex defaults to "female")
            if getattr(other, 'sex', 'female') == self.sex:
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

    def _find_protective_male(self, flock: list):
        if self.sex != "female":
            return None

        preferred = getattr(self, "mate_partner", None)
        if (preferred is not None
                and preferred.alive
                and preferred.dead_state is None
                and getattr(preferred, "sex", "female") == "male"):
            ddx = preferred.tx - self.tx
            ddy = preferred.ty - self.ty
            dist_sq = ddx * ddx + ddy * ddy
            if 0.0 < dist_sq <= PROTECTOR_SEARCH_RADIUS * PROTECTOR_SEARCH_RADIUS:
                dist = math.sqrt(dist_sq)
                return preferred, ddx / dist, ddy / dist, dist

        best_score = float("inf")
        best = None
        for other in flock:
            if other is self or other.dead_state is not None or not other.is_adult:
                continue
            if getattr(other, "sex", "female") != "male":
                continue
            ddx = other.tx - self.tx
            ddy = other.ty - self.ty
            dist_sq = ddx * ddx + ddy * ddy
            if dist_sq <= 0.0 or dist_sq > PROTECTOR_SEARCH_RADIUS * PROTECTOR_SEARCH_RADIUS:
                continue
            dist = math.sqrt(dist_sq)
            same_herd_bias = -6.0 if self.herd_id >= 0 and other.herd_id == self.herd_id else 0.0
            fertile_bias = -2.0 if other.hunger < other._reproduce_threshold else 0.0
            score = dist + same_herd_bias + fertile_bias
            if score < best_score:
                best_score = score
                best = (other, ddx / dist, ddy / dist, dist)
        return best

    # ------------------------------------------------------------------
    # Reproduction — now sets pregnancy instead of immediate birth
    # ------------------------------------------------------------------

    def _try_reproduce(self, flock: list, grid: list, new_sheep: list):
        if self.sex == "male":      # males don't carry pregnancies
            return
        if self.infertile or self.pregnant:
            return
        for other in flock:
            if other is self or other.dead_state is not None or not other.is_adult or other.infertile or other.pregnant:
                continue
            # Require opposite sex
            if getattr(other, 'sex', 'female') == self.sex:
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
                baby_hp = int(round(max(HP_MIN, min(HP_MAX,
                    (self.genetic_hp + other.genetic_hp) / 2.0 + random.gauss(0, 1.5)))))
                baby_social = max(1, min(10, round(
                    (self.genetic_social + other.genetic_social) / 2.0 + random.gauss(0, 0.8))))
                baby_strength = max(1.0 - GENETIC_STRENGTH_RANGE,
                                    min(1.0 + GENETIC_STRENGTH_RANGE,
                                        (self.genetic_strength + other.genetic_strength) / 2.0
                                        + random.gauss(0, GENETIC_STRENGTH_RANGE * 0.12)))
                baby_sex = "male" if random.random() < MALE_BIRTH_CHANCE else "female"
                pending.append((baby_size, baby_speed, baby_maturity, baby_lifespan,
                                baby_gestation, baby_hp, baby_social, baby_strength, baby_sex))

            # Gestation time: genetic base × nutrition stress of the mother
            # Poor nutrition extends pregnancy (fewer resources for foetal development)
            nutrition_delay = max(0.0, self._avg_hunger - 0.3) * 0.5
            base_gestation  = (GESTATION_BASE * self.genetic_gestation
                               + GESTATION_PER_LAMB * (litter_count - 1))
            self.pregnant               = True
            self.gestation_timer        = base_gestation * (1.0 + nutrition_delay)
            self._gestation_hunger_mult = GESTATION_HUNGER_BASE + GESTATION_HUNGER_SCALE * (litter_count - 1)
            self._pending_litter        = pending
            self.mate_partner = other
            other.mate_partner = self

            self.reproduce_cooldown  = REPRODUCE_COOLDOWN
            # Males get a brief cooldown so they can keep mating with other females
            other.reproduce_cooldown = 30.0 if getattr(other, 'sex', 'female') == 'male' else REPRODUCE_COOLDOWN
            return

    def _birth(self, grid: list, new_sheep: list):
        """Spawn pending offspring when gestation completes."""
        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        for baby_data in self._pending_litter:
            (baby_size, baby_speed, baby_maturity, baby_lifespan,
             baby_gestation, baby_hp, baby_social, baby_strength, baby_sex) = baby_data
            attempts = 0
            while attempts < 8:
                attempts += 1
                ox = self.tx + random.uniform(-2.0, 2.0)
                oy = self.ty + random.uniform(-2.0, 2.0)
                c, r = int(ox), int(oy)
                if is_walkable_tile(grid, r, c):
                    kwargs = dict(age=0.0,
                                  genetic_size=baby_size,
                                  genetic_maturity=baby_maturity,
                                  genetic_lifespan=baby_lifespan,
                                  genetic_gestation=baby_gestation,
                                  genetic_hp=baby_hp,
                                  genetic_social=baby_social,
                                  genetic_strength=baby_strength)
                    if _OFFSPRING_FACTORY is not None:
                        baby = _OFFSPRING_FACTORY(ox, oy, sex=baby_sex, **kwargs)
                    else:
                        baby = Sheep(ox, oy, **kwargs)
                        baby.sex = baby_sex
                    baby.hunger  = 0.0
                    baby.speed   = baby_speed
                    baby.herd_id = self.herd_id
                    baby.parent  = self
                    new_sheep.append(baby)
                    break

    # ------------------------------------------------------------------
    # Death helpers
    # ------------------------------------------------------------------

    def _die(self):
        """Transition a living sheep into the fresh-corpse state."""
        corpse_meat = max(6.0, self.genetic_size * self.genetic_hp * MEAT_PER_SIZE_UNIT)
        if self.age < self.maturity_age:
            corpse_meat *= 0.5
        self.dead_state   = "fresh"
        self.death_timer  = DAY_DURATION * (0.60 + 0.010 * self.genetic_hp + 0.16 * self.genetic_size)
        self.death_facing = self.facing
        # Stop movement — corpse is inert
        self.state = Sheep.IDLE
        self.dx    = 0.0
        self.dy    = 0.0
        # Remove from active herd
        self.herd_id        = -1
        self.pregnant       = False
        self._pending_litter = []
        # Meat available to wolves — scales with body size and vitality
        self.meat_value = corpse_meat
        self.max_meat_value = corpse_meat
        self.corpse_decay_rate = 1.0
        if self.age < self.maturity_age:
            self._corpse_slot_count = 2
        else:
            self._corpse_slot_count = 4
        self._max_eaters = self._corpse_slot_count

    def _update_corpse(self, dt: float, grid: list, regrowth_timers: dict,
                       dirty_callback=None):
        """Tick the corpse state machine."""
        self.death_timer -= dt * max(1.0, self.corpse_decay_rate)
        self.corpse_decay_rate = 1.0
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

        # Decay wolf fear over time
        if self._wolf_fear_timer > 0:
            self._wolf_fear_timer -= dt
            if self._wolf_fear_timer <= 0:
                self.wolf_aware   = False
                self.wolf_flee_dx = 0.0
                self.wolf_flee_dy = 0.0

        # Gestation tick — birth when timer expires
        if self.pregnant:
            self.gestation_timer -= dt
            if self.gestation_timer <= 0:
                self._birth(grid, new_sheep)
                self.pregnant        = False
                self._pending_litter = []

        # Hunger doesn't increase while actively eating
        if self.state != Sheep.EAT:
            hunger_mult    = self._gestation_hunger_mult if self.pregnant else 1.0
            hp_hunger_mult = 1.0 + (self.genetic_hp - HP_MIN) * 0.01
            self.hunger = min(1.0, self.hunger + HUNGER_RATE * self.genetic_size * hp_hunger_mult * hunger_mult * self._hunger_rate_mult * dt)

        # Nutrition stress: exponential moving average over ~1 sim-day window
        alpha = dt / DAY_DURATION
        self._avg_hunger += (self.hunger - self._avg_hunger) * min(1.0, alpha)

        # HP drains only when hunger is completely maxed — sheep with food barely lose HP
        if self.hunger >= 1.0:
            self.hp = max(0.0, self.hp - HP_DRAIN_RATE * dt)

        # --- Snow exposure damage ---
        cur_row, cur_col = int(self.ty), int(self.tx)
        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        on_snow = (0 <= cur_row < rows and 0 <= cur_col < cols
                   and grid[cur_row][cur_col] == SNOW)
        if on_snow:
            self.snow_exposure += dt
            if self.snow_exposure >= SNOW_EXPOSURE_THRESHOLD:
                self.hp = max(0.0, self.hp - SNOW_DAMAGE_RATE * dt)
        else:
            self.snow_exposure = 0.0

        # --- Death checks ---
        if self.age >= self._effective_lifespan or self.hp <= 0:
            self._die()
            return

        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        col  = int(self.tx)
        row  = int(self.ty)
        on_map   = 0 <= row < rows and 0 <= col < cols
        on_grass = on_map and grid[row][col] == GRASS
        grass_dir = self._find_herd_grass(grid, rows, cols)
        protector = self._find_protective_male(flock)

        urgency = 0.0
        if self.hunger >= HUNGER_URGENCY_THRESHOLD:
            urgency = (self.hunger - HUNGER_URGENCY_THRESHOLD) / (1.0 - HUNGER_URGENCY_THRESHOLD)

        starving = self.hunger >= STARVING_THRESHOLD

        # --- EAT ---
        if self.state == Sheep.EAT:
            self.hunger = max(0.0, self.hunger - EAT_RATE * dt)
            self.hp = min(self.max_hp, self.hp + HP_EAT_REGEN * dt)
            if self.timer <= 0 or self.hunger <= 0.1:
                self._schedule_idle()
            return

        if self.hunger >= HUNGER_THRESHOLD and on_grass:
            # Always eat current tile
            grid[row][col] = DIRT
            regrowth_timers[(row, col)] = REGROWTH_TIME
            if dirty_callback:
                dirty_callback(row, col)
            # Extra tiles eaten based on body size:
            #   bottom 20% (size < 0.91) → 1 tile total (0 extras)
            #   lower-middle (0.91–1.015) → 2 tiles (1 extra)
            #   upper-middle (1.015–1.12) → 3 tiles (2 extras)
            #   top 10% (size >= 1.12)   → 4 tiles (3 extras)
            s = self.genetic_size
            if s < 0.91:
                n_extra = 0
            elif s < 1.015:
                n_extra = 1
            elif s < 1.12:
                n_extra = 2
            else:
                n_extra = 3
            if n_extra > 0:
                neighbors = [(row, col + 1), (row + 1, col), (row + 1, col + 1)]
                random.shuffle(neighbors)
                for nr, nc in neighbors[:n_extra]:
                    if (0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] == GRASS):
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
                        direction = self._find_herd_grass(grid, rows, cols)
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

                if (not on_grass and grass_dir is not None
                        and self.hunger < HUNGER_THRESHOLD * 0.95):
                    self.state = Sheep.WALK
                    self.dx, self.dy = grass_dir
                    self.timer = random.uniform(1.6, 2.6)
                    self._refresh_facing()
                    return

                if not self._try_follow(flock):
                    if self.hunger >= HUNGER_THRESHOLD * 0.7:
                        # Prefer grass within herd territory; only wander outside if solo
                        direction = grass_dir
                        if direction:
                            self.state = Sheep.WALK
                            self.dx, self.dy = direction
                            self.timer = 2.0
                            self._refresh_facing()
                        else:
                            # No grass in herd territory — drift toward herd center and wait
                            self._schedule_walk(flock)
                    else:
                        self._schedule_walk(flock)

        elif self.state == Sheep.WALK and self.timer <= 0:
            self._schedule_idle()

        # --- Movement ---
        if self.state == Sheep.WALK:
            sx, sy = self._separation_delta(flock, dt)

            # Continuous cohesion correction: counteracts the separation asymmetry.
            # Separation is applied every frame; cohesion was only at walk-scheduling.
            # This gentle per-frame pull (proportional to excess distance) rebalances that.
            if self.herd_id >= 0 and not self.migration_mode:
                hpx = self.herd_cx - self.tx
                hpy = self.herd_cy - self.ty
                hpdist = math.sqrt(hpx * hpx + hpy * hpy)
                if hpdist > SEPARATION_RADIUS:
                    excess = hpdist - SEPARATION_RADIUS
                    pull_mag = excess * WALK_COHESION_BOOST * (self.genetic_social / SOCIAL_NORM) * dt
                    sx += (hpx / hpdist) * pull_mag
                    sy += (hpy / hpdist) * pull_mag

            if grass_dir is not None:
                gx, gy = grass_dir
                grass_weight = OFF_GRASS_PULL_WEIGHT if not on_grass else GRASS_PULL_WEIGHT
                if self.wolf_aware:
                    grass_weight *= 0.2
                sx += gx * grass_weight * dt
                sy += gy * grass_weight * dt

            if protector is not None:
                _, pdx, pdy, pdist = protector
                separated = (self.herd_id < 0
                             or pdist > 4.0
                             or math.hypot(self.herd_cx - self.tx, self.herd_cy - self.ty)
                             > self.herd_awareness_r * 0.45)
                if separated and not self.wolf_aware:
                    protector_pull = PROTECTOR_PULL_WEIGHT * dt
                    sx += pdx * protector_pull
                    sy += pdy * protector_pull

            if self.wolf_aware:
                herd_dx = self.herd_cx - self.tx
                herd_dy = self.herd_cy - self.ty
                herd_dist = math.hypot(herd_dx, herd_dy)
                if herd_dist > 2.2:
                    sx += (herd_dx / herd_dist) * FLEE_HERD_WEIGHT * dt
                    sy += (herd_dy / herd_dist) * FLEE_HERD_WEIGHT * dt

                if grass_dir is not None and not on_grass:
                    gx, gy = grass_dir
                    sx += gx * FLEE_GRASS_WEIGHT * dt
                    sy += gy * FLEE_GRASS_WEIGHT * dt

            speed_mult = 1.0 + urgency * 1.8
            fear_mult = 2.0 if self.wolf_aware else 1.0
            new_tx = self.tx + self.dx * self.move_speed * speed_mult * fear_mult * dt + sx
            new_ty = self.ty + self.dy * self.move_speed * speed_mult * fear_mult * dt + sy

            move_tx, move_ty, blocked = advance_until_blocked(
                grid, self.tx, self.ty, new_tx, new_ty
            )
            self.tx = move_tx
            self.ty = move_ty
            if blocked:
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
        sx_center_f  = self.tx * tile_size - cam_x
        sy_center_f  = self.ty * tile_size - cam_y
        sx_center    = round(sx_center_f)
        sy_center    = round(sy_center_f)

        # --- LOD: flat colored dot when zoomed far out ---
        if tile_size < Sheep.LOD_THRESHOLD:
            dot_r = max(1, round(effective_ts * 0.6))
            color = self._avg_colors.get(key, (220, 220, 220))
            pygame.draw.circle(screen, color, (sx_center, sy_center), dot_r)
            return

        sprite = self._scaled(key, effective_ts)
        w, h   = sprite.get_size()
        sx     = round(sx_center_f - w / 2)
        sy     = round(sy_center_f - h / 2)
        screen.blit(sprite, (sx, sy))

        # HP bar — shown only when injured
        if self.dead_state is None and self.hp < self.max_hp:
            bar_w   = w
            bar_h   = max(2, round(effective_ts) // 7)
            bar_y   = sy - bar_h - 2
            hp_frac = max(0.0, self.hp / self.max_hp)
            filled  = int(bar_w * hp_frac)
            pygame.draw.rect(screen, (40, 40, 40), (sx, bar_y, bar_w, bar_h))
            rc = int((1.0 - hp_frac) * 220)
            gc = int(hp_frac * 200)
            pygame.draw.rect(screen, (rc, gc, 30), (sx, bar_y, filled, bar_h))
