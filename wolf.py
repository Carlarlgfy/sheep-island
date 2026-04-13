"""
Wolf — apex predator for Sheep Island.

Behaviour summary
-----------------
• Hunts sheep (preferring weak, young, and old prey); attacks with lunges.
• Rams fight back and deal counter-damage; wolf flees when HP is low.
• Eats fresh sheep corpses (not decayed); shared corpse depleted as wolves feed.
• Reproduces sexually (male + female); smaller litters than sheep, higher pup mortality.
• Slower hunger accumulation than sheep — stays satiated for several in-game days.
• Forms packs (managed by WolfPackManager) for coordinated hunts.
• Wolf corpses follow the same fresh → decayed → gone pattern as sheep.
"""

import math
import os
import random

import pygame

from mapgen import WATER, GRASS

_WOLF_DIR = os.path.join(os.path.dirname(__file__), "brown gray female wolf")

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

# Hunger
WOLF_HUNGER_RATE          = 0.00115   # ~14.5 sim-min from 0→1  (sheep: ~3.7 min)
WOLF_HUNGER_HUNT          = 0.45      # start actively hunting above this
WOLF_HUNGER_DESPERATE     = 0.72      # ignore ram risk, push through everything
WOLF_EAT_RATE             = 0.12      # meat units consumed per sim-second while eating
WOLF_HUNGER_PER_MEAT      = 0.055     # hunger reduction per meat unit consumed
WOLF_EAT_REGEN            = 0.20      # HP restored per second while eating

# HP
WOLF_HP_MIN               = 18
WOLF_HP_MAX               = 40
WOLF_HP_DRAIN_RATE        = 1.0 / 35.0   # HP/sec when hunger >= 1.0
WOLF_HP_REGEN_RATE        = 0.12         # HP/sec when idle and satiated (hunger < 0.35)
WOLF_FLEE_HP_FRAC         = 0.28         # flee when HP < this fraction of max

# Speed
WOLF_SPEED_MIN            = 5.0    # tiles/sec  (sheep max = 6.0)
WOLF_SPEED_MAX            = 9.5
WOLF_LUNGE_SPEED          = 12.0   # tiles/sec during lunge charge

# Combat
WOLF_ATTACK_RANGE         = 1.4    # tiles — must be this close to trigger a lunge
WOLF_ATTACK_COOLDOWN      = 2.2    # seconds between attacks on same target
WOLF_LUNGE_DURATION       = 0.55   # seconds per lunge animation
WOLF_DAMAGE_BASE          = (3.5, 7.5)    # (min, max) damage per lunge
RAM_COUNTER_DAMAGE        = (1.5, 4.5)    # damage ram deals back per lunge
WOLF_SCAN_INTERVAL        = 3.5    # seconds between full prey-scan sweeps
WOLF_HEAR_RADIUS          = 600.0  # tile radius — hear moving creatures
WOLF_SCENT_RADIUS         = 150.0  # tile radius — passively scent stationary prey
WOLF_SMELL_RADIUS         = 1000.0 # tile radius — smell fresh corpses
WOLF_SCARE_RADIUS         = 18.0   # tile radius within which nearby sheep panic
WOLF_SCARE_DURATION       = 18.0   # seconds sheep stay scared after a wolf is near

# Corpse feasting limit
WOLF_MAX_EATERS_MIN       = 2      # minimum concurrent feeders per corpse
WOLF_MAX_EATERS_MAX       = 4      # maximum concurrent feeders per corpse

# Meat
MEAT_PER_SIZE_UNIT        = 6.0    # sheep.genetic_size × this = meat_value on death
WOLF_SMELL_MEAT_THRESHOLD = 0.08   # corpse ignored only when nearly full (hunger below this)

# Rival pack contest
WOLF_RIVAL_SCARE_RADIUS   = 12.0   # range at which a larger pack scares rival wolves off a corpse

# Reproduction
WOLF_REPRODUCE_COOLDOWN   = 3000.0  # ~10 days between matings
WOLF_GESTATION_BASE       = 1350.0  # ~4.5 days base gestation
WOLF_GESTATION_RANGE      = 0.18    # ±18% heritable modifier
WOLF_LITTER_MIN           = 1
WOLF_LITTER_MAX           = 4
WOLF_PUP_MORTALITY        = 0.35    # fraction of pups that die in first 2 days
WOLF_REPRODUCE_HUNGER     = 0.38    # must not be hungry to reproduce
WOLF_MATE_RADIUS          = 8.0     # tiles for mating

# Lifespan / maturation
WOLF_LIFESPAN_MIN         = 5400.0  # 18 days
WOLF_LIFESPAN_MAX         = 8700.0  # 29 days
WOLF_LIFESPAN_RANGE       = 0.10    # ±10% heritable modifier
WOLF_MATURITY_AGE_BASE    = 1200.0  # 4 days

# Genetics ranges (multiplicative around 1.0)
WOLF_SIZE_RANGE           = 0.15
WOLF_STRENGTH_RANGE       = 0.20
WOLF_AWARENESS_RANGE      = 0.15
WOLF_GESTATION_RANGE      = 0.18

# Pup early-mortality tracking
WOLF_PUP_DEATH_DAILY      = 0.18   # 18%/day chance of pup dying until maturity
DAY_DURATION              = 300.0  # sim-seconds per in-game day

# Post-maturity earned growth (battle-hardening / well-fed bulk)
WOLF_EARN_SIZE_MAX        = 0.30   # max extra size fraction earned through good feeding
WOLF_EARN_STR_MAX         = 0.30   # max extra strength fraction earned through combat
WOLF_EARN_SIZE_RATE       = 0.00005 # earned-size gain per sim-second while satiated adult
WOLF_EARN_STR_PER_LUNGE   = 0.003  # earned-strength gain per lunge delivered (combat exp)

# Corpse timers (wolves decay at similar rate to sheep)
WOLF_CORPSE_FRESH_MIN     = DAY_DURATION * 2
WOLF_CORPSE_FRESH_MAX     = DAY_DURATION * 3
WOLF_CORPSE_DECAYED_MIN   = DAY_DURATION * 2
WOLF_CORPSE_DECAYED_MAX   = DAY_DURATION * 3

# Movement / separation
WOLF_SEPARATION_RADIUS    = 1.4
WOLF_SEPARATION_FORCE     = 1.6
WOLF_AWARENESS_BASE       = 52.0   # base tile radius; scaled by genetic_awareness

# Idle wander
WOLF_WANDER_INTERVAL_MIN  = 4.0
WOLF_WANDER_INTERVAL_MAX  = 9.0
WOLF_PATROL_INTERVAL_MIN  = 6.0
WOLF_PATROL_INTERVAL_MAX  = 14.0

# Pack cohesion (pulls wolves back toward pack center when they stray)
WOLF_PACK_COHESION_FORCE  = 3.5    # tiles/sec pull toward center
WOLF_PACK_COHESION_INNER  = 12.0   # tiles from center before cohesion starts pulling
WOLF_PACK_MAX_STRAY       = 45.0   # tiles — beyond this a wolf abandons its hunt and returns

# Sheep territory avoidance (when not hungry enough to hunt)
WOLF_SHEEP_AVOID_RADIUS   = 28.0   # tile radius — detect nearby sheep clusters
WOLF_SHEEP_AVOID_MIN_N    = 4      # minimum sheep in radius to trigger avoidance
WOLF_SHEEP_AVOID_FORCE    = 1.5    # push speed (tiles/sec)

# Social play (satiated pack members chase and play with each other)
WOLF_PLAY_HUNGER_MAX      = 0.22   # wolf must be below this hunger to play
WOLF_PLAY_CHANCE          = 0.0006 # probability per sim-second of initiating play
WOLF_PLAY_RADIUS          = 12.0   # tile radius to find a play partner
WOLF_PLAY_CHASE_DURATION  = 18.0   # sim-seconds the chaser pursues
WOLF_PLAY_SUBMIT_DURATION = 7.0    # sim-seconds the caught wolf stays submissive
WOLF_PLAY_SPEED_MULT      = 0.85   # speed multiplier during play chase (slower than hunt)

# Post-feed lounge (wolves camp near kill site for 1–2 sim-days after a big meal)
WOLF_LOUNGE_HUNGER_TRIGGER = 0.15  # enter lounge when hunger drops below this on EAT exit
WOLF_LOUNGE_MIN            = DAY_DURATION * 1.0   # 300 sim-seconds = 1 day
WOLF_LOUNGE_MAX            = DAY_DURATION * 2.0   # 600 sim-seconds = 2 days
WOLF_LOUNGE_IDLE_MIN       = 18.0  # lounge idle period min (longer rests than normal)
WOLF_LOUNGE_IDLE_MAX       = 45.0
WOLF_LOUNGE_WALK_MIN       = 4.0   # short walk bursts so they don't stray far
WOLF_LOUNGE_WALK_MAX       = 9.0
WOLF_LOUNGE_DRIFT_RADIUS   = 10.0  # tiles from anchor before drift-back kicks in
WOLF_LOUNGE_DRIFT_FORCE    = 1.6   # pull-back strength (tiles/sec)
WOLF_LOUNGE_HUNT_HUNGER    = 0.65  # only break lounge to hunt above this hunger


# ---------------------------------------------------------------------------
# Wolf
# ---------------------------------------------------------------------------

class Wolf:
    IDLE        = "idle"
    WALK        = "walk"
    HUNT        = "hunt"
    LUNGE       = "lunge"
    EAT         = "eat"
    FLEE        = "flee"
    LOUNGE      = "lounge"
    PLAY_CHASE  = "play_chase"
    PLAY_SUBMIT = "play_submit"

    _sprites_raw: dict | None = None
    _cache: dict = {}
    _avg_colors: dict = {}

    LOD_THRESHOLD = 6.0

    # ------------------------------------------------------------------
    # Sprite loading
    # ------------------------------------------------------------------

    @classmethod
    def load_sprites(cls):
        if cls._sprites_raw is not None:
            return

        def _load(name):
            return pygame.image.load(
                os.path.join(_WOLF_DIR, name)
            ).convert_alpha()

        right      = _load("right side facing female gray wolf.png")
        left       = pygame.transform.flip(right, True, False)

        front      = _load("standing female gray wolf.png")
        behind     = _load("backward facing gray female wolf.png")

        sit_right  = _load("sitting wolf facing right.png")
        sit_left   = pygame.transform.flip(sit_right, True, False)
        sit_front  = _load("sitting gray wolf.png")

        eat_right  = _load("gray female wolf rght facing eating.png")
        eat_left   = pygame.transform.flip(eat_right, True, False)
        eat_front  = _load("gray female facing forward eating.png")

        lunge_r    = _load("gray female wolf lunging .png")
        lunge_l    = pygame.transform.flip(lunge_r, True, False)

        dead_r     = _load("dead wolf facing right.png")
        dead_l     = pygame.transform.flip(dead_r, True, False)
        decay_r    = _load("decaying femal wolf corpse facing right.png")
        decay_l    = pygame.transform.flip(decay_r, True, False)

        cls._sprites_raw = {
            "right":          right,
            "left":           left,
            "front":          front,
            "behind":         behind,
            "idle_right":     sit_right,
            "idle_left":      sit_left,
            "idle_front":     sit_front,
            "idle_behind":    behind,     # reuse behind for idle-away
            "eat_right":      eat_right,
            "eat_left":       eat_left,
            "eat_front":      eat_front,
            "eat_behind":     eat_front,  # no dedicated back-eating sprite
            "lunge_right":    lunge_r,
            "lunge_left":     lunge_l,
            "dead_right":     dead_r,
            "dead_left":      dead_l,
            "decayed_right":  decay_r,
            "decayed_left":   decay_l,
        }
        cls._cache = {}
        cls._avg_colors = {k: cls._sample_avg_color(v)
                           for k, v in cls._sprites_raw.items()}

    @classmethod
    def _sample_avg_color(cls, surf: pygame.Surface) -> tuple:
        w, h = surf.get_size()
        xs   = [int(w * (i + 0.5) / 10) for i in range(10)]
        ys   = [int(h * (j + 0.5) / 10) for j in range(10)]
        r_s = g_s = b_s = cnt = 0
        for x in xs:
            for y in ys:
                c = surf.get_at((x, y))
                if c.a > 32:
                    r_s += c.r; g_s += c.g; b_s += c.b; cnt += 1
        if cnt == 0:
            return (170, 150, 120)
        return (r_s // cnt, g_s // cnt, b_s // cnt)

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
                 genetic_awareness: float = None,
                 genetic_hp: int = None,
                 genetic_lifespan: float = None,
                 genetic_gestation: float = None):

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

        # --- Genetics ---
        def _cg(val, r):
            return max(1.0 - r, min(1.0 + r, val))

        self.genetic_size = (
            _cg(genetic_size, WOLF_SIZE_RANGE) if genetic_size is not None
            else random.uniform(1.0 - WOLF_SIZE_RANGE, 1.0 + WOLF_SIZE_RANGE)
        )
        self.genetic_strength = (
            _cg(genetic_strength, WOLF_STRENGTH_RANGE) if genetic_strength is not None
            else random.uniform(1.0 - WOLF_STRENGTH_RANGE, 1.0 + WOLF_STRENGTH_RANGE)
        )
        self.genetic_awareness = (
            _cg(genetic_awareness, WOLF_AWARENESS_RANGE) if genetic_awareness is not None
            else random.uniform(1.0 - WOLF_AWARENESS_RANGE, 1.0 + WOLF_AWARENESS_RANGE)
        )
        self.genetic_hp = (
            int(max(WOLF_HP_MIN, min(WOLF_HP_MAX, genetic_hp))) if genetic_hp is not None
            else random.randint(WOLF_HP_MIN, WOLF_HP_MAX)
        )
        self.genetic_lifespan = (
            _cg(genetic_lifespan, WOLF_LIFESPAN_RANGE) if genetic_lifespan is not None
            else random.uniform(1.0 - WOLF_LIFESPAN_RANGE, 1.0 + WOLF_LIFESPAN_RANGE)
        )
        self.genetic_gestation = (
            _cg(genetic_gestation, WOLF_GESTATION_RANGE) if genetic_gestation is not None
            else random.uniform(1.0 - WOLF_GESTATION_RANGE, 1.0 + WOLF_GESTATION_RANGE)
        )

        self.speed = (
            float(genetic_speed)
            if genetic_speed is not None
            else random.uniform(WOLF_SPEED_MIN, WOLF_SPEED_MAX)
        )

        # Derived timing
        self.maturity_age = WOLF_MATURITY_AGE_BASE
        self.lifespan     = (
            random.uniform(WOLF_LIFESPAN_MIN, WOLF_LIFESPAN_MAX) * self.genetic_lifespan
        )
        self.age = float(age) if age is not None else random.uniform(
            self.maturity_age, self.maturity_age * 2.5
        )
        self.hp      = float(self.genetic_hp)
        self.hunger  = random.uniform(0.05, 0.40)

        # Reproduction
        self.pregnant              = False
        self.gestation_timer       = 0.0
        self._pending_litter: list = []
        self.reproduce_cooldown    = random.uniform(0, WOLF_REPRODUCE_COOLDOWN * 0.4)

        # Hunt state
        self._hunt_target          = None    # ref to target Sheep
        self._scan_timer           = random.uniform(0, WOLF_SCAN_INTERVAL)
        self._attack_cooldown      = 0.0
        self._lunge_timer          = 0.0
        self._lunge_active         = False

        # Flee state
        self._flee_timer           = 0.0
        self._flee_cx              = tile_x
        self._flee_cy              = tile_y

        # Pack info (written by WolfPackManager)
        self.pack_id               = -1
        self.pack_cx               = tile_x
        self.pack_cy               = tile_y
        self.pack_hunt_target      = None    # shared pack target
        self.pack_size             = 1
        self.pack_awareness_radius = 30.0   # written each frame by WolfPackManager

        # Social play
        self._play_target       = None   # ref to packmate being chased
        self._play_timer        = 0.0    # counts down chase or submit duration

        # Post-feed lounge
        self._lounge_timer      = 0.0
        self._lounge_anchor_x   = float(tile_x)
        self._lounge_anchor_y   = float(tile_y)

        # Pup mortality tracking
        self._pup_death_timer      = 0.0   # countdown for daily pup-mortality check

        # Earned growth (accumulated post-maturity; 0–1 each)
        self.earned_size           = 0.0   # extra bulk from good feeding
        self.earned_strength       = 0.0   # extra power from combat experience

        # Corpse state
        self.alive       = True
        self.dead_state  = None   # None / "fresh" / "decayed"
        self.death_timer = 0.0
        self.death_facing = "right"

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_adult(self) -> bool:
        return self.age >= self.maturity_age

    @property
    def size_scale(self) -> float:
        growth = 0.55 + 0.45 * min(1.0, self.age / self.maturity_age)
        size_bonus = 1.0 + self.earned_size * WOLF_EARN_SIZE_MAX
        return growth * self.genetic_size * size_bonus

    @property
    def awareness_radius(self) -> float:
        return WOLF_AWARENESS_BASE * self.genetic_awareness

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _refresh_facing(self):
        if abs(self.dx) >= abs(self.dy):
            self.facing = "right" if self.dx >= 0 else "left"
        else:
            self.facing = "front" if self.dy > 0 else "behind"

    def _separation_delta(self, wolves: list, dt: float) -> tuple:
        sx = sy = 0.0
        for other in wolves:
            if other is self or other.dead_state is not None:
                continue
            ddx  = self.tx - other.tx
            ddy  = self.ty - other.ty
            dist = math.sqrt(ddx * ddx + ddy * ddy)
            if 0 < dist < WOLF_SEPARATION_RADIUS:
                strength = (WOLF_SEPARATION_RADIUS - dist) / WOLF_SEPARATION_RADIUS
                sx += (ddx / dist) * strength * WOLF_SEPARATION_FORCE * dt
                sy += (ddy / dist) * strength * WOLF_SEPARATION_FORCE * dt
        return sx, sy

    def _score_prey(self, sheep) -> float:
        """Higher = better prey target. Prefer weak, young, old."""
        score = 0.0
        if not hasattr(sheep, 'maturity_age'):
            return score
        # Young
        if sheep.age < sheep.maturity_age:
            score += 3.0
        # Old
        elif sheep.age > sheep.lifespan * 0.70:
            score += 2.0
        # Weak HP
        max_hp = float(sheep.genetic_hp)
        if max_hp > 0:
            hp_frac = sheep.hp / max_hp
            if hp_frac < 0.30:
                score += 4.0
            elif hp_frac < 0.60:
                score += 1.5
        return score

    def _find_best_prey(self, sheep_list: list):
        """Return the best living-sheep target within sensory range, or None.

        Wolves cannot see — they hear moving prey up to WOLF_HEAR_RADIUS and
        passively scent stationary prey up to WOLF_SCENT_RADIUS.
        """
        hear_r_sq   = WOLF_HEAR_RADIUS  ** 2
        scent_r_sq  = WOLF_SCENT_RADIUS ** 2
        best_score   = -1.0
        best_target  = None
        best_dist_sq = float('inf')

        for sheep in sheep_list:
            if sheep.dead_state is not None or not sheep.alive:
                continue
            ddx     = sheep.tx - self.tx
            ddy     = sheep.ty - self.ty
            dist_sq = ddx * ddx + ddy * ddy

            # Determine detection range: hear moving prey, scent stationary prey
            is_moving = (abs(getattr(sheep, 'dx', 0.0)) + abs(getattr(sheep, 'dy', 0.0))) > 0.01
            limit_sq  = hear_r_sq if is_moving else scent_r_sq
            if dist_sq > limit_sq:
                continue

            score = self._score_prey(sheep)
            if score > best_score or (score == best_score and dist_sq < best_dist_sq):
                best_score   = score
                best_target  = sheep
                best_dist_sq = dist_sq

        return best_target

    def _find_nearest_corpse(self, sheep_list: list, wolf_list: list = None):
        """Return the nearest fresh sheep corpse with meat remaining, or None.

        Wolves can smell corpses up to WOLF_SMELL_RADIUS (1000 tiles).
        Skips corpses held by a rival pack that is larger than this wolf's pack.
        Wolves that are nearly full (hunger < WOLF_SMELL_MEAT_THRESHOLD) ignore corpses.
        """
        if self.hunger < WOLF_SMELL_MEAT_THRESHOLD:
            return None
        smell_sq     = WOLF_SMELL_RADIUS ** 2
        best_dist_sq = float('inf')
        best_corpse  = None

        for sheep in sheep_list:
            if sheep.dead_state != "fresh":
                continue
            if getattr(sheep, 'meat_value', 0.0) <= 0:
                continue
            ddx     = sheep.tx - self.tx
            ddy     = sheep.ty - self.ty
            dist_sq = ddx * ddx + ddy * ddy
            if dist_sq >= smell_sq or dist_sq >= best_dist_sq:
                continue

            # Skip if a larger rival pack controls this corpse
            if wolf_list is not None:
                rival_size = self._rival_pack_size_at(sheep, wolf_list)
                if rival_size > self.pack_size:
                    continue

            best_dist_sq = dist_sq
            best_corpse  = sheep

        return best_corpse

    def _rival_pack_size_at(self, corpse, wolf_list: list) -> int:
        """Return the size of the largest rival pack with wolves near this corpse."""
        check_sq = (WOLF_RIVAL_SCARE_RADIUS * 2.5) ** 2
        rival_packs: dict[int, int] = {}
        for w in wolf_list:
            if w is self or not w.alive or w.dead_state is not None:
                continue
            if w.pack_id < 0 or w.pack_id == self.pack_id:
                continue
            ddx = w.tx - corpse.tx
            ddy = w.ty - corpse.ty
            if ddx * ddx + ddy * ddy <= check_sq:
                rival_packs[w.pack_id] = rival_packs.get(w.pack_id, 0) + 1
        return max(rival_packs.values()) if rival_packs else 0

    def _scare_rival_pack_at_corpse(self, corpse, wolf_list: list):
        """When approaching a corpse our larger pack wants, scare off rival wolves."""
        if self.pack_size <= 1:
            return
        scare_sq = WOLF_RIVAL_SCARE_RADIUS ** 2
        for w in wolf_list:
            if w is self or not w.alive or w.dead_state is not None:
                continue
            if w.pack_id < 0 or w.pack_id == self.pack_id:
                continue
            if w.pack_size >= self.pack_size:
                continue   # rival pack is equal or bigger — don't try to bluff
            ddx = w.tx - corpse.tx
            ddy = w.ty - corpse.ty
            if ddx * ddx + ddy * ddy > scare_sq:
                continue
            # Drive the rival wolf off
            if w.state in (Wolf.EAT, Wolf.HUNT, Wolf.IDLE, Wolf.WALK, Wolf.LOUNGE):
                w.state        = Wolf.FLEE
                w._flee_timer  = random.uniform(20.0, 40.0)
                w._flee_cx     = self.tx
                w._flee_cy     = self.ty
                w._hunt_target = None

    def _scare_nearby_sheep(self, sheep_list: list):
        """Set wolf_aware on sheep within WOLF_SCARE_RADIUS."""
        scare_sq = WOLF_SCARE_RADIUS ** 2
        for sheep in sheep_list:
            if sheep.dead_state is not None or not sheep.alive:
                continue
            ddx     = sheep.tx - self.tx
            ddy     = sheep.ty - self.ty
            dist_sq = ddx * ddx + ddy * ddy
            if dist_sq > scare_sq or dist_sq == 0:
                continue
            dist = math.sqrt(dist_sq)
            sheep.wolf_aware       = True
            sheep._wolf_fear_timer = WOLF_SCARE_DURATION
            # Direction away from wolf
            sheep.wolf_flee_dx = (sheep.tx - self.tx) / dist
            sheep.wolf_flee_dy = (sheep.ty - self.ty) / dist

    # ------------------------------------------------------------------
    # Pack cohesion / sheep avoidance / play helpers
    # ------------------------------------------------------------------

    def _pack_cohesion_delta(self, dt: float) -> tuple:
        """Pull toward pack center when wolf has strayed beyond the inner cohesion threshold."""
        if self.pack_size <= 1:
            return 0.0, 0.0
        ddx  = self.pack_cx - self.tx
        ddy  = self.pack_cy - self.ty
        dist = math.sqrt(ddx * ddx + ddy * ddy)
        if dist <= WOLF_PACK_COHESION_INNER or dist < 0.001:
            return 0.0, 0.0
        # Scale linearly: 0 at inner threshold, full at 3× inner threshold
        t    = min(1.0, (dist - WOLF_PACK_COHESION_INNER) / max(1.0, WOLF_PACK_COHESION_INNER * 2))
        pull = WOLF_PACK_COHESION_FORCE * t * dt
        return (ddx / dist) * pull, (ddy / dist) * pull

    def _sheep_avoidance_delta(self, sheep_list: list, dt: float) -> tuple:
        """Gently push wolf away from nearby sheep clusters when not hunting."""
        if self.hunger >= WOLF_HUNGER_HUNT:
            return 0.0, 0.0
        avoid_sq = WOLF_SHEEP_AVOID_RADIUS ** 2
        sx = sy = 0.0
        count    = 0
        for sheep in sheep_list:
            if sheep.dead_state is not None or not sheep.alive:
                continue
            ddx = sheep.tx - self.tx
            ddy = sheep.ty - self.ty
            if ddx * ddx + ddy * ddy <= avoid_sq:
                sx    += sheep.tx
                sy    += sheep.ty
                count += 1
        if count < WOLF_SHEEP_AVOID_MIN_N:
            return 0.0, 0.0
        # Push away from the cluster centroid
        cx   = sx / count
        cy   = sy / count
        fdx  = self.tx - cx
        fdy  = self.ty - cy
        dist = math.sqrt(fdx * fdx + fdy * fdy)
        if dist < 0.001:
            return 0.0, 0.0
        t    = max(0.0, 1.0 - dist / WOLF_SHEEP_AVOID_RADIUS)
        push = WOLF_SHEEP_AVOID_FORCE * t * dt
        return (fdx / dist) * push, (fdy / dist) * push

    def _find_play_partner(self, wolf_list: list):
        """Return a nearby satiated packmate to play with, or None."""
        play_sq = WOLF_PLAY_RADIUS ** 2
        best    = None
        best_d  = float('inf')
        for w in wolf_list:
            if (w is self or not w.alive or w.dead_state is not None
                    or w.pack_id != self.pack_id or w.pack_id < 0
                    or w.hunger >= WOLF_PLAY_HUNGER_MAX
                    or w.state in (Wolf.HUNT, Wolf.LUNGE, Wolf.EAT, Wolf.FLEE,
                                   Wolf.PLAY_CHASE, Wolf.PLAY_SUBMIT)):
                continue
            ddx = w.tx - self.tx
            ddy = w.ty - self.ty
            d   = ddx * ddx + ddy * ddy
            if d <= play_sq and d < best_d:
                best   = w
                best_d = d
        return best

    def _enter_lounge(self):
        """Trigger post-feed lounge if wolf is very satiated."""
        if self.hunger <= WOLF_LOUNGE_HUNGER_TRIGGER and self._lounge_timer <= 0:
            self._lounge_timer    = random.uniform(WOLF_LOUNGE_MIN, WOLF_LOUNGE_MAX)
            self._lounge_anchor_x = self.tx
            self._lounge_anchor_y = self.ty

    # ------------------------------------------------------------------
    # Combat
    # ------------------------------------------------------------------

    def _do_lunge_damage(self, target) -> bool:
        """Deal damage to target; ram may counter. Returns True if target died."""
        str_mult = 1.0 + self.earned_strength * WOLF_EARN_STR_MAX
        dmg = self.genetic_strength * str_mult * random.uniform(*WOLF_DAMAGE_BASE)
        target.hp = max(0.0, target.hp - dmg)

        # Ram counter-attack
        from ram import Ram
        if isinstance(target, Ram) and target.dead_state is None:
            counter = getattr(target, 'genetic_strength', 1.0) * random.uniform(*RAM_COUNTER_DAMAGE)
            self.hp = max(0.0, self.hp - counter)

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
        if self.pregnant or self.reproduce_cooldown > 0:
            return
        if self.hunger >= WOLF_REPRODUCE_HUNGER:
            return

        mate_sq = WOLF_MATE_RADIUS ** 2
        for other in wolf_list:
            if (other is self or other.dead_state is not None or not other.alive
                    or not other.is_adult or other.sex != "male"
                    or other.reproduce_cooldown > 0
                    or other.hunger >= WOLF_REPRODUCE_HUNGER):
                continue
            ddx = other.tx - self.tx
            ddy = other.ty - self.ty
            if ddx * ddx + ddy * ddy > mate_sq:
                continue

            # Determine litter — apply pup mortality immediately
            raw_litter = random.randint(WOLF_LITTER_MIN, WOLF_LITTER_MAX)
            survivors  = sum(1 for _ in range(raw_litter)
                             if random.random() > WOLF_PUP_MORTALITY)
            litter_count = max(1, survivors)

            def _inherit(a, b, attr, r):
                mid = (getattr(a, attr) + getattr(b, attr)) / 2.0
                return max(1.0 - r, min(1.0 + r, mid + random.gauss(0, r * 0.12)))

            pending = []
            for _ in range(litter_count):
                baby_sex = "male" if random.random() < 0.5 else "female"
                pending.append({
                    "sex":      baby_sex,
                    "size":     _inherit(self, other, "genetic_size",    WOLF_SIZE_RANGE),
                    "strength": _inherit(self, other, "genetic_strength", WOLF_STRENGTH_RANGE),
                    "aware":    _inherit(self, other, "genetic_awareness", WOLF_AWARENESS_RANGE),
                    "hp":       int(round(max(WOLF_HP_MIN, min(WOLF_HP_MAX,
                                    (self.genetic_hp + other.genetic_hp) / 2.0
                                    + random.gauss(0, 1.5))))),
                    "lifespan": _inherit(self, other, "genetic_lifespan", WOLF_LIFESPAN_RANGE),
                    "gestation":_inherit(self, other,"genetic_gestation", WOLF_GESTATION_RANGE),
                    "speed":    max(WOLF_SPEED_MIN * 0.75, min(WOLF_SPEED_MAX * 1.15,
                                    (self.speed + other.speed) / 2.0
                                    + random.gauss(0, 0.3))),
                })

            self.pregnant        = True
            self.gestation_timer = (WOLF_GESTATION_BASE * self.genetic_gestation
                                    + 120.0 * (litter_count - 1))
            self._pending_litter = pending
            self.reproduce_cooldown  = WOLF_REPRODUCE_COOLDOWN
            other.reproduce_cooldown = 60.0
            return

    def _birth(self, grid: list, new_wolves: list):
        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        for data in self._pending_litter:
            attempts = 0
            while attempts < 8:
                attempts += 1
                ox = self.tx + random.uniform(-2.0, 2.0)
                oy = self.ty + random.uniform(-2.0, 2.0)
                c, r = int(ox), int(oy)
                if 0 <= r < rows and 0 <= c < cols and grid[r][c] != WATER:
                    pup = Wolf(ox, oy,
                               age=0.0,
                               sex=data["sex"],
                               genetic_size=data["size"],
                               genetic_strength=data["strength"],
                               genetic_awareness=data["aware"],
                               genetic_hp=data["hp"],
                               genetic_lifespan=data["lifespan"],
                               genetic_gestation=data["gestation"],
                               genetic_speed=data["speed"])
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
        # Wolf corpses don't have meat_value (wolves don't eat other wolves)

    def _update_corpse(self, dt: float):
        self.death_timer -= dt
        if self.death_timer <= 0:
            if self.dead_state == "fresh":
                self.dead_state  = "decayed"
                self.death_timer = random.uniform(WOLF_CORPSE_DECAYED_MIN,
                                                  WOLF_CORPSE_DECAYED_MAX)
            elif self.dead_state == "decayed":
                self.alive = False

    # ------------------------------------------------------------------
    # Movement helpers
    # ------------------------------------------------------------------

    def _move_toward(self, tx: float, ty: float, dt: float,
                     grid: list, wolves: list, speed_mult: float = 1.0):
        """Move toward (tx, ty), apply separation + pack cohesion, clamp to non-water."""
        ddx  = tx - self.tx
        ddy  = ty - self.ty
        dist = math.sqrt(ddx * ddx + ddy * ddy)
        if dist > 0.05:
            self.dx = ddx / dist
            self.dy = ddy / dist
        self._refresh_facing()

        sx, sy = self._separation_delta(wolves, dt)
        px, py = self._pack_cohesion_delta(dt)
        spd    = self.speed * speed_mult
        new_tx = self.tx + self.dx * spd * dt + sx + px
        new_ty = self.ty + self.dy * spd * dt + sy + py

        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        nc, nr = int(new_tx), int(new_ty)
        if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] != WATER:
            self.tx = new_tx
            self.ty = new_ty

    # ------------------------------------------------------------------
    # Main update
    # ------------------------------------------------------------------

    def update(self, dt: float, grid: list, sheep_list: list,
               wolf_list: list, new_wolves: list):
        if not self.alive:
            return

        # Corpse state
        if self.dead_state is not None:
            self._update_corpse(dt)
            return

        # --- Age and timers ---
        self.age += dt
        self.timer -= dt
        if self._attack_cooldown > 0:
            self._attack_cooldown = max(0.0, self._attack_cooldown - dt)
        if self._scan_timer > 0:
            self._scan_timer = max(0.0, self._scan_timer - dt)
        if self.reproduce_cooldown > 0:
            self.reproduce_cooldown = max(0.0, self.reproduce_cooldown - dt)
        if self._play_timer > 0:
            self._play_timer = max(0.0, self._play_timer - dt)
        if self._lounge_timer > 0:
            self._lounge_timer = max(0.0, self._lounge_timer - dt)

        # Pup daily mortality check
        if not self.is_adult:
            self._pup_death_timer -= dt
            if self._pup_death_timer <= 0:
                self._pup_death_timer = DAY_DURATION
                if random.random() < WOLF_PUP_DEATH_DAILY:
                    self._die()
                    return

        # --- Hunger & HP ---
        if self.state != Wolf.EAT:
            self.hunger = min(1.0, self.hunger + WOLF_HUNGER_RATE * self.genetic_size * dt)
        if self.hunger >= 1.0:
            self.hp = max(0.0, self.hp - WOLF_HP_DRAIN_RATE * dt)
        # Idle regen when satiated
        if self.state in (Wolf.IDLE, Wolf.LOUNGE, Wolf.PLAY_SUBMIT) and self.hunger < 0.35:
            self.hp = min(float(self.genetic_hp), self.hp + WOLF_HP_REGEN_RATE * dt)

        # --- Old age / HP death ---
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

        # Validate hunt target
        if self._hunt_target is not None:
            t = self._hunt_target
            if not t.alive or t.dead_state is not None:
                # Target died — move to eat it if fresh
                if t.dead_state == "fresh" and getattr(t, 'meat_value', 0) > 0:
                    self.state        = Wolf.HUNT
                    self._hunt_target = t  # will be picked up as corpse
                else:
                    self._hunt_target = None
                    if self.state in (Wolf.HUNT, Wolf.LUNGE):
                        self.state = Wolf.WALK
                        self.timer = random.uniform(WOLF_WANDER_INTERVAL_MIN,
                                                    WOLF_WANDER_INTERVAL_MAX)

        # ================================================================
        # State machine
        # ================================================================

        # ── FLEE ────────────────────────────────────────────────────────
        if self.state == Wolf.FLEE:
            self._flee_timer -= dt
            # Flee away from the threat center
            fx = self.tx - self._flee_cx
            fy = self.ty - self._flee_cy
            fd = math.sqrt(fx * fx + fy * fy)
            if fd > 0:
                self.dx = fx / fd
                self.dy = fy / fd
            self._refresh_facing()
            sx, sy = self._separation_delta(wolf_list, dt)
            new_tx = self.tx + self.dx * self.speed * 1.4 * dt + sx
            new_ty = self.ty + self.dy * self.speed * 1.4 * dt + sy
            rows = len(grid); cols = len(grid[0]) if rows else 0
            nc, nr = int(new_tx), int(new_ty)
            if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] != WATER:
                self.tx = new_tx; self.ty = new_ty
            # Stop fleeing when timer up AND HP partly recovered
            if self._flee_timer <= 0 and self.hp >= float(self.genetic_hp) * 0.45:
                self.state = Wolf.IDLE
                self.timer = random.uniform(WOLF_WANDER_INTERVAL_MIN, WOLF_WANDER_INTERVAL_MAX)
            return

        # ── LOUNGE ──────────────────────────────────────────────────────
        if self.state == Wolf.LOUNGE:
            # Break lounge if desperately hungry
            if self.hunger >= WOLF_LOUNGE_HUNT_HUNGER:
                self._lounge_timer = 0.0
                self.state         = Wolf.IDLE
                self.timer         = random.uniform(WOLF_WANDER_INTERVAL_MIN,
                                                    WOLF_WANDER_INTERVAL_MAX)
                # fall through to IDLE/WALK hunt logic this frame

            else:
                # Lounge expired naturally
                if self._lounge_timer <= 0:
                    self.state = Wolf.IDLE
                    self.timer = random.uniform(WOLF_WANDER_INTERVAL_MIN, WOLF_WANDER_INTERVAL_MAX)
                    return

                # Drift back toward anchor if wolf has wandered too far
                ddx  = self._lounge_anchor_x - self.tx
                ddy  = self._lounge_anchor_y - self.ty
                dist = math.sqrt(ddx * ddx + ddy * ddy)
                if dist > WOLF_LOUNGE_DRIFT_RADIUS:
                    # Override walk direction toward anchor
                    self.dx = ddx / dist
                    self.dy = ddy / dist
                    self._refresh_facing()

                # Reproduction is fine while lounging
                if (self.is_adult and self.sex == "female"
                        and not self.pregnant and self.reproduce_cooldown <= 0
                        and self.hunger < WOLF_REPRODUCE_HUNGER):
                    self._try_reproduce(wolf_list, new_wolves)

                # Idle/walk oscillation with lounge biases
                if self.timer <= 0:
                    # Opportunity to play while lounging (satiated)
                    if (self.hunger < WOLF_PLAY_HUNGER_MAX and self.pack_size > 1
                            and random.random() < WOLF_PLAY_CHANCE * dt * 60.0):
                        partner = self._find_play_partner(wolf_list)
                        if partner is not None:
                            partner.state       = Wolf.PLAY_SUBMIT
                            partner._play_timer = WOLF_PLAY_CHASE_DURATION * 1.5
                            self.state          = Wolf.PLAY_CHASE
                            self._play_target   = partner
                            self._play_timer    = WOLF_PLAY_CHASE_DURATION
                            return

                    if self.dx == 0.0 and self.dy == 0.0:
                        # Currently resting — switch to brief walk
                        angle   = random.uniform(0, 2 * math.pi)
                        self.dx = math.cos(angle)
                        self.dy = math.sin(angle)
                        self._refresh_facing()
                        self.timer = random.uniform(WOLF_LOUNGE_WALK_MIN, WOLF_LOUNGE_WALK_MAX)
                    else:
                        # Currently walking — switch to longer rest
                        self.dx    = 0.0
                        self.dy    = 0.0
                        self.timer = random.uniform(WOLF_LOUNGE_IDLE_MIN, WOLF_LOUNGE_IDLE_MAX)

                # Walk movement with separation + cohesion
                if self.dx != 0.0 or self.dy != 0.0:
                    sx, sy = self._separation_delta(wolf_list, dt)
                    px, py = self._pack_cohesion_delta(dt)
                    new_tx = self.tx + self.dx * self.speed * 0.6 * dt + sx + px
                    new_ty = self.ty + self.dy * self.speed * 0.6 * dt + sy + py
                    rows = len(grid); cols = len(grid[0]) if rows else 0
                    nc, nr = int(new_tx), int(new_ty)
                    if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] != WATER:
                        self.tx = new_tx; self.ty = new_ty
                    else:
                        angle   = random.uniform(0, 2 * math.pi)
                        self.dx = math.cos(angle)
                        self.dy = math.sin(angle)
                        self._refresh_facing()
                return

        # ── PLAY_SUBMIT ─────────────────────────────────────────────────
        if self.state == Wolf.PLAY_SUBMIT:
            # Break out if hungry
            if self.hunger >= WOLF_HUNGER_HUNT:
                self.state = Wolf.IDLE
                self.timer = random.uniform(WOLF_WANDER_INTERVAL_MIN, WOLF_WANDER_INTERVAL_MAX)
                return
            if self._play_timer <= 0:
                self.state = Wolf.IDLE
                self.timer = random.uniform(WOLF_WANDER_INTERVAL_MIN, WOLF_WANDER_INTERVAL_MAX)
            self.dx = 0.0
            self.dy = 0.0
            return

        # ── PLAY_CHASE ──────────────────────────────────────────────────
        if self.state == Wolf.PLAY_CHASE:
            target = self._play_target
            # Validate target — abort if it left or started doing something serious
            invalid = (target is None or not target.alive or target.dead_state is not None
                       or target.state in (Wolf.HUNT, Wolf.LUNGE, Wolf.EAT, Wolf.FLEE))
            if invalid or self._play_timer <= 0:
                self._play_target = None
                self.state = Wolf.IDLE
                self.timer = random.uniform(WOLF_WANDER_INTERVAL_MIN, WOLF_WANDER_INTERVAL_MAX)
                return

            ddx  = target.tx - self.tx
            ddy  = target.ty - self.ty
            dist = math.sqrt(ddx * ddx + ddy * ddy)

            if dist <= WOLF_ATTACK_RANGE:
                # Caught! target rolls over
                if target.state in (Wolf.IDLE, Wolf.WALK, Wolf.LOUNGE, Wolf.PLAY_SUBMIT):
                    target.state       = Wolf.PLAY_SUBMIT
                    target._play_timer = WOLF_PLAY_SUBMIT_DURATION
                    target.dx          = 0.0
                    target.dy          = 0.0
                self._play_target = None
                self.state = Wolf.IDLE
                self.timer = random.uniform(WOLF_WANDER_INTERVAL_MIN, WOLF_WANDER_INTERVAL_MAX)
                return

            self._move_toward(target.tx, target.ty, dt, grid, wolf_list,
                               speed_mult=WOLF_PLAY_SPEED_MULT)
            return

        # ── EAT ─────────────────────────────────────────────────────────
        if self.state == Wolf.EAT:
            corpse = self._hunt_target
            if (corpse is None or corpse.dead_state != "fresh"
                    or getattr(corpse, 'meat_value', 0) <= 0):
                # Corpse gone or depleted — done eating
                self._hunt_target = None
                self._enter_lounge()
                if self._lounge_timer > 0:
                    self.state = Wolf.LOUNGE
                else:
                    self.state = Wolf.IDLE
                self.timer = random.uniform(WOLF_WANDER_INTERVAL_MIN, WOLF_WANDER_INTERVAL_MAX)
                return

            # Consume meat
            bite      = WOLF_EAT_RATE * dt
            available = corpse.meat_value
            consumed  = min(bite, available)
            corpse.meat_value -= consumed

            self.hunger = max(0.0, self.hunger - consumed * WOLF_HUNGER_PER_MEAT)
            self.hp     = min(float(self.genetic_hp), self.hp + WOLF_EAT_REGEN * dt)

            # When meat depleted, fast-track corpse to decayed
            if corpse.meat_value <= 0:
                corpse.dead_state  = "decayed"
                corpse.death_timer = random.uniform(30.0, 90.0)   # decays quickly
                self._hunt_target  = None
                self._enter_lounge()
                if self._lounge_timer > 0:
                    self.state = Wolf.LOUNGE
                else:
                    self.state = Wolf.IDLE
                self.timer = random.uniform(WOLF_WANDER_INTERVAL_MIN, WOLF_WANDER_INTERVAL_MAX)
            return

        # ── LUNGE ───────────────────────────────────────────────────────
        if self.state == Wolf.LUNGE:
            target = self._hunt_target
            if target is None or target.dead_state is not None:
                self.state = Wolf.HUNT if self._hunt_target is not None else Wolf.WALK
                self.timer = random.uniform(2.0, 4.0)
                self._lunge_active = False
                return

            self._lunge_timer -= dt
            # Charge toward target at lunge speed
            self._move_toward(target.tx, target.ty, dt, grid, wolf_list,
                               speed_mult=WOLF_LUNGE_SPEED / max(self.speed, 1.0))

            if self._lunge_timer <= 0:
                # Deliver damage
                killed = self._do_lunge_damage(target)
                self._attack_cooldown = WOLF_ATTACK_COOLDOWN
                self._lunge_active    = False

                # Battle-hardening: gain strength from each successful lunge
                if self.is_adult and self.earned_strength < 1.0:
                    self.earned_strength = min(1.0,
                        self.earned_strength + WOLF_EARN_STR_PER_LUNGE)

                # Check wolf HP — flee if badly hurt
                if self.hp < float(self.genetic_hp) * WOLF_FLEE_HP_FRAC:
                    self.state       = Wolf.FLEE
                    self._flee_timer = 25.0
                    self._flee_cx    = target.tx
                    self._flee_cy    = target.ty
                    self._hunt_target = None
                    return

                if killed:
                    # Assign feasting limit on the fresh corpse
                    target._max_eaters = random.randint(WOLF_MAX_EATERS_MIN,
                                                        WOLF_MAX_EATERS_MAX)
                    # Killer always gets to eat immediately
                    self.state = Wolf.EAT
                    # hunt_target still points to the now-fresh corpse
                else:
                    # Continue hunting
                    self.state = Wolf.HUNT
            return

        # ── HUNT ────────────────────────────────────────────────────────
        if self.state == Wolf.HUNT:
            # Check if we should flee first
            if self.hp < float(self.genetic_hp) * WOLF_FLEE_HP_FRAC:
                tx_flee = self._hunt_target.tx if self._hunt_target else self.tx
                ty_flee = self._hunt_target.ty if self._hunt_target else self.ty
                self.state       = Wolf.FLEE
                self._flee_timer = 25.0
                self._flee_cx    = tx_flee
                self._flee_cy    = ty_flee
                self._hunt_target = None
                return

            # Pack cohesion enforcement — too far from packmates, return first
            if self.pack_size > 1:
                ddx_p = self.pack_cx - self.tx
                ddy_p = self.pack_cy - self.ty
                pack_dist = math.sqrt(ddx_p * ddx_p + ddy_p * ddy_p)
                if pack_dist > WOLF_PACK_MAX_STRAY:
                    self._hunt_target = None
                    self.state = Wolf.WALK
                    self.dx    = ddx_p / pack_dist
                    self.dy    = ddy_p / pack_dist
                    self._refresh_facing()
                    self.timer = 10.0
                    return

            target = self._hunt_target

            # If target is now a fresh corpse — approach and eat (or scare rivals)
            if target is not None and target.dead_state == "fresh":
                ddx  = target.tx - self.tx
                ddy  = target.ty - self.ty
                dist = math.sqrt(ddx * ddx + ddy * ddy)
                # Scare any rival pack feeding on this corpse
                self._scare_rival_pack_at_corpse(target, wolf_list)
                if dist < 1.2 and getattr(target, 'meat_value', 0) > 0:
                    max_eat = getattr(target, '_max_eaters', WOLF_MAX_EATERS_MAX)
                    cur_eat = sum(1 for w in wolf_list
                                  if w is not self and w.alive
                                  and w.dead_state is None
                                  and w.state == Wolf.EAT
                                  and w._hunt_target is target)
                    if cur_eat < max_eat:
                        self.state = Wolf.EAT
                    # else: corpse is full — wait nearby, retry next frame
                    return
                if dist > 0.05:
                    self._move_toward(target.tx, target.ty, dt, grid, wolf_list)
                return

            # Periodically rescan for a better target
            if self._scan_timer <= 0:
                self._scan_timer = WOLF_SCAN_INTERVAL
                # Corpses always take priority over live prey
                corpse = self._find_nearest_corpse(sheep_list, wolf_list)
                if corpse is not None:
                    self._hunt_target = corpse
                    return
                # Follow the pack's shared live target when possible
                pack_t = self.pack_hunt_target
                if (pack_t is not None and pack_t.alive
                        and pack_t.dead_state is None):
                    target = pack_t
                    self._hunt_target = target
                else:
                    new_target = self._find_best_prey(sheep_list)
                    if new_target is not None:
                        self._hunt_target = new_target
                        target            = new_target
                    elif self.hunger < WOLF_HUNGER_HUNT:
                        # Not hungry and no easy prey — stand down
                        self._hunt_target = None
                        self.state = Wolf.WALK
                        self.timer = random.uniform(WOLF_PATROL_INTERVAL_MIN,
                                                    WOLF_PATROL_INTERVAL_MAX)
                        return

            if target is None:
                self.state = Wolf.WALK
                self.timer = random.uniform(WOLF_PATROL_INTERVAL_MIN, WOLF_PATROL_INTERVAL_MAX)
                return

            # Scare sheep in vicinity
            self._scare_nearby_sheep(sheep_list)

            # Move toward prey
            ddx  = target.tx - self.tx
            ddy  = target.ty - self.ty
            dist = math.sqrt(ddx * ddx + ddy * ddy)

            if dist <= WOLF_ATTACK_RANGE and self._attack_cooldown <= 0:
                # Initiate lunge
                self.state         = Wolf.LUNGE
                self._lunge_timer  = WOLF_LUNGE_DURATION
                self._lunge_active = True
                return

            self._move_toward(target.tx, target.ty, dt, grid, wolf_list)
            return

        # ── IDLE / WALK ──────────────────────────────────────────────────

        # Should we start hunting?
        should_hunt = (self.hunger >= WOLF_HUNGER_HUNT or
                       (self.pack_hunt_target is not None
                        and self.pack_hunt_target.alive
                        and self.pack_hunt_target.dead_state is None))
        # Corpses always take priority — check them first (smell range 1000 tiles)
        if self._scan_timer <= 0:
            corpse = self._find_nearest_corpse(sheep_list, wolf_list)
            if corpse is not None:
                self._hunt_target = corpse
                self.state        = Wolf.HUNT
                self._scan_timer  = WOLF_SCAN_INTERVAL
                return

        if should_hunt and self._scan_timer <= 0:
            self._scan_timer = WOLF_SCAN_INTERVAL
            target = None
            if (self.pack_hunt_target is not None
                    and self.pack_hunt_target.alive
                    and self.pack_hunt_target.dead_state is None):
                target = self.pack_hunt_target
            if target is None:
                target = self._find_best_prey(sheep_list)
            if target is not None:
                self._hunt_target = target
                self.state        = Wolf.HUNT
                return

        # Reproduction
        if (self.is_adult and self.sex == "female"
                and not self.pregnant and self.reproduce_cooldown <= 0
                and self.hunger < WOLF_REPRODUCE_HUNGER):
            self._try_reproduce(wolf_list, new_wolves)

        # Social play: satiated idle wolf may initiate a chase with a packmate
        if (self.state == Wolf.IDLE and self.pack_size > 1
                and self.hunger < WOLF_PLAY_HUNGER_MAX
                and random.random() < WOLF_PLAY_CHANCE * dt):
            partner = self._find_play_partner(wolf_list)
            if partner is not None:
                # Partner waits patiently; chaser gets full duration to arrive
                partner.state       = Wolf.PLAY_SUBMIT
                partner._play_timer = WOLF_PLAY_CHASE_DURATION * 1.5
                self.state          = Wolf.PLAY_CHASE
                self._play_target   = partner
                self._play_timer    = WOLF_PLAY_CHASE_DURATION
                return

        if self.timer <= 0:
            if self.state == Wolf.IDLE:
                # Switch to wander
                angle   = random.uniform(0, 2 * math.pi)
                self.dx = math.cos(angle)
                self.dy = math.sin(angle)
                self._refresh_facing()
                self.state = Wolf.WALK
                self.timer = random.uniform(WOLF_PATROL_INTERVAL_MIN, WOLF_PATROL_INTERVAL_MAX)
            else:
                # Switch to rest
                self.state = Wolf.IDLE
                self.timer = random.uniform(WOLF_WANDER_INTERVAL_MIN, WOLF_WANDER_INTERVAL_MAX)
                self.dx    = 0.0
                self.dy    = 0.0
                return

        if self.state == Wolf.WALK:
            sx, sy = self._separation_delta(wolf_list, dt)
            ax, ay = self._sheep_avoidance_delta(sheep_list, dt)
            px, py = self._pack_cohesion_delta(dt)
            new_tx = self.tx + self.dx * self.speed * dt + sx + ax + px
            new_ty = self.ty + self.dy * self.speed * dt + sy + ay + py
            rows = len(grid); cols = len(grid[0]) if rows else 0
            nc, nr = int(new_tx), int(new_ty)
            if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] != WATER:
                self.tx = new_tx; self.ty = new_ty
            else:
                # Hit boundary — pick new direction
                angle   = random.uniform(0, 2 * math.pi)
                self.dx = math.cos(angle)
                self.dy = math.sin(angle)
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
        elif self.state == Wolf.LUNGE:
            key = "lunge_left" if self.facing == "left" else "lunge_right"
        elif self.state == Wolf.EAT:
            key = f"eat_{self.facing}"
        elif self.state in (Wolf.IDLE, Wolf.LOUNGE, Wolf.PLAY_SUBMIT):
            key = f"idle_{self.facing}"
        else:
            key = self.facing

        effective_ts = tile_size * self.size_scale
        ts           = max(1, round(tile_size))
        sx_center    = int(self.tx * ts - cam_x)
        sy_center    = int(self.ty * ts - cam_y)

        if tile_size < Wolf.LOD_THRESHOLD:
            dot_r = max(1, round(effective_ts * 0.65))
            color = self._avg_colors.get(key, (170, 150, 120))
            pygame.draw.circle(screen, color, (sx_center, sy_center), dot_r)
            return

        sprite = self._scaled(key, effective_ts)
        w, h   = sprite.get_size()
        sx     = sx_center - w // 2
        sy     = sy_center - h // 2
        screen.blit(sprite, (sx, sy))

        # HP bar — show only when injured
        if self.dead_state is None and self.hp < float(self.genetic_hp):
            bar_w   = w
            bar_h   = max(2, round(effective_ts) // 7)
            bar_y   = sy - bar_h - 2
            hp_frac = max(0.0, self.hp / float(self.genetic_hp))
            filled  = int(bar_w * hp_frac)
            pygame.draw.rect(screen, (40, 40, 40), (sx, bar_y, bar_w, bar_h))
            rc = int((1.0 - hp_frac) * 220)
            gc = int(hp_frac * 200)
            pygame.draw.rect(screen, (rc, gc, 30), (sx, bar_y, filled, bar_h))
