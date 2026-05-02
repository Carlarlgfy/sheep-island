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

from mapgen import WATER, GRASS, SNOW, is_walkable_tile, advance_until_blocked

_WOLF_DIR = os.path.join(os.path.dirname(__file__), "fauna", "wolf")

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

# Hunger
WOLF_HUNGER_RATE          = 0.00028   # slow accumulation — wolves are built for feast/famine
WOLF_HUNGER_HUNT          = 0.42      # start actively hunting above this
WOLF_HUNGER_DESPERATE     = 0.72      # ignore ram risk, push through everything
WOLF_HUNGER_STARVING      = 0.88      # only starving wolves should return to old corpses
WOLF_EAT_RATE             = 0.65      # deliberate eating — feast should be visible and last a minute+
WOLF_HUNGER_PER_MEAT      = 0.28      # balanced with larger meat pool per carcass
WOLF_EAT_REGEN            = 0.20      # HP restored per second while eating

# HP
WOLF_HP_MIN               = 18
WOLF_HP_MAX               = 40
WOLF_HP_DRAIN_RATE        = 1.0 / 35.0   # HP/sec when hunger >= 1.0
WOLF_HP_REGEN_RATE        = 0.12         # HP/sec when idle and satiated (hunger < 0.35)
WOLF_FLEE_HP_FRAC         = 0.28         # flee when HP < this fraction of max

# Speed
WOLF_SPEED_MIN            = 4.8    # tiles/sec
WOLF_SPEED_MAX            = 8.8
WOLF_SPEED_RANGE          = 0.16   # heritable speed bias before body-size penalties
WOLF_LUNGE_SPEED          = 10.5   # tiles/sec during lunge charge
WOLF_LUNGE_RECOVER_SPEED  = 4.8    # tiles/sec during hop-back after impact
WOLF_LUNGE_RECOVER_TIME   = 0.22
WOLF_LUNGE_STOP_DIST      = 0.95   # stop just shy of the sheep instead of overlapping it

# Combat
WOLF_ATTACK_RANGE         = 1.4    # tiles — must be this close to trigger a lunge
WOLF_ATTACK_COOLDOWN      = 2.2    # seconds between attacks on same target
WOLF_LUNGE_DURATION       = 0.55   # seconds per lunge animation
WOLF_DAMAGE_BASE          = (3.5, 7.5)    # (min, max) damage per lunge
RAM_COUNTER_DAMAGE        = (1.5, 4.5)    # damage ram deals back per lunge
WOLF_SCAN_INTERVAL        = 3.5    # seconds between full prey-scan sweeps
WOLF_HEAR_RADIUS          = 600.0  # tile radius — hear moving creatures
WOLF_SCENT_RADIUS         = 150.0  # tile radius — passively scent stationary prey
WOLF_SMELL_RADIUS         = 700.0  # tile radius — smell fresh corpses
WOLF_SCARE_RADIUS         = 120.0  # sheep should strongly avoid wolf presence
WOLF_SCARE_DURATION       = 18.0   # seconds sheep stay scared after a wolf is near

# Corpse feasting limit
WOLF_MAX_EATERS_MIN       = 3      # minimum concurrent feeders per corpse
WOLF_MAX_EATERS_MAX       = 6      # maximum concurrent feeders per corpse

# Meat
MEAT_PER_SIZE_UNIT        = 14.0   # enough for the whole pack; depletes in ~15-25s of feasting
WOLF_SMELL_MEAT_THRESHOLD = 0.08   # corpse ignored only when nearly full (hunger below this)

# Rival pack contest
WOLF_RIVAL_SCARE_RADIUS   = 12.0   # range at which a larger pack scares rival wolves off a corpse

# Reproduction
WOLF_REPRODUCE_COOLDOWN   = 1400.0  # ~4.7 days between litters
WOLF_GESTATION_BASE       = 1000.0  # ~3.3 day gestation
WOLF_GESTATION_PER_CUB    = 0.0     # keep gestation close to base total
WOLF_GESTATION_RANGE      = 0.18    # ±18% heritable modifier
WOLF_LITTER_MIN           = 4
WOLF_LITTER_MAX           = 6       # occasional bigger litters
WOLF_PUP_MORTALITY        = 0.12    # 12% at-birth mortality (was 20%)
WOLF_REPRODUCE_HUNGER     = 0.46    # can breed at moderate hunger
WOLF_MATE_RADIUS          = 15.0    # tiles for mating — pack spreads out so wider range

# Lifespan / maturation
WOLF_LIFESPAN_MIN         = 6000.0  # 20 days
WOLF_LIFESPAN_MAX         = 12000.0 # 40 days
WOLF_LIFESPAN_RANGE       = 0.10    # ±10% heritable modifier
WOLF_MATURITY_AGE_BASE    = 1100.0  # ~3.7 days — pups mature a bit faster

# Genetics ranges (multiplicative around 1.0)
WOLF_SIZE_RANGE           = 0.18
WOLF_STRENGTH_RANGE       = 0.20
WOLF_AWARENESS_RANGE      = 0.15
WOLF_GESTATION_RANGE      = 0.18

# Pup early-mortality tracking
WOLF_PUP_DEATH_DAILY      = 0.08   # 8%/day — pups are hard but the pack protects them
DAY_DURATION              = 300.0  # sim-seconds per in-game day

# Snow exposure damage (wolves are hardier than sheep in the cold)
WOLF_SNOW_EXPOSURE_THRESHOLD = 450.0   # 1.5 days in snow before damage starts
WOLF_SNOW_DAMAGE_RATE        = 0.2     # HP/sec once threshold is exceeded

# Pup feeding (regurgitation — satiated adults feed nearby offspring)
WOLF_PUP_FEED_RADIUS        = 9.0             # tiles — adult must be this close to a pup
WOLF_PUP_FEED_AMOUNT        = 0.55            # hunger units reduced in pup per regurgitation
WOLF_PUP_FEED_INTERVAL      = 35.0            # seconds between feedings (per adult wolf)
WOLF_PUP_FEED_MIN_COOLDOWN  = DAY_DURATION    # adult must have ≥1 day of satiation left
WOLF_EAT_MAX_SESSION      = DAY_DURATION * 0.09  # ~27 sec max eating — quick but visible
WOLF_CORPSE_APPROACH_MAX  = 10.0                 # abandon a corpse if slot takes too long to reach
WOLF_CORPSE_COMMIT_MAX    = DAY_DURATION * 0.13  # ~39 sec total commitment (approach + eating)
WOLF_MEAL_COOLDOWN_MIN    = DAY_DURATION * 1.5   # 1.5 days before hunting again
WOLF_MEAL_COOLDOWN_MAX    = DAY_DURATION * 2.5   # 2.5 days max satiation window

# Post-maturity earned growth (battle-hardening / well-fed bulk)
WOLF_EARN_SIZE_MAX        = 0.30   # max extra size fraction earned through good feeding
WOLF_EARN_STR_MAX         = 0.30   # max extra strength fraction earned through combat
WOLF_EARN_SIZE_RATE       = 0.00005 # earned-size gain per sim-second while satiated adult
WOLF_EARN_STR_PER_LUNGE   = 0.003  # earned-strength gain per lunge delivered (combat exp)

# Corpse timers (wolves decay at similar rate to sheep)
WOLF_CORPSE_FRESH_MIN     = DAY_DURATION * 1
WOLF_CORPSE_FRESH_MAX     = DAY_DURATION * 2
WOLF_CORPSE_DECAYED_MIN   = DAY_DURATION * 1
WOLF_CORPSE_DECAYED_MAX   = DAY_DURATION * 2

# Movement / separation
WOLF_SEPARATION_FORCE     = 2.4
WOLF_AWARENESS_BASE       = 52.0   # base tile radius; scaled by genetic_awareness

# Idle wander
WOLF_WANDER_INTERVAL_MIN  = 4.0
WOLF_WANDER_INTERVAL_MAX  = 9.0
WOLF_PATROL_INTERVAL_MIN  = 6.0
WOLF_PATROL_INTERVAL_MAX  = 14.0

# Pack cohesion (pulls wolves back toward pack center when they stray)
WOLF_PACK_COHESION_FORCE  = 14.0   # much stronger pull so packs stay visibly bunched
WOLF_PACK_COHESION_INNER  = 7.0    # wolves should idle in a compact knot around the core
WOLF_PACK_MAX_STRAY       = 42.0   # only very unusual pursuits should peel wolves off the pack
WOLF_PACK_ALPHA_FOLLOW_DIST = 4.5  # most wolves should hang close to the alpha
WOLF_PACK_ALPHA_PULL      = 12.0   # non-alphas actively follow the alpha instead of drifting
WOLF_PACK_TRAVEL_PULL     = 9.5
WOLF_PACK_FORMATION_SPACING = 2.6
WOLF_PACK_FORMATION_DEPTH = 3.4
WOLF_PACK_VENTURE_RADIUS  = 600.0
WOLF_PACK_RETURN_RADIUS   = 24.0
WOLF_PACK_RETURN_PULL     = 4.0
WOLF_REST_HUNGER_PAUSE_MAX = 0.26

# Size / sex presentation and appetite
WOLF_BASE_DRAW_SCALE      = 1.20   # all wolves render 20% larger than before
WOLF_MALE_SIZE_MULT       = 1.25   # temporary male dimorphism while sharing sprites
WOLF_SIZE_HP_SCALE        = 0.22
WOLF_SIZE_SPEED_PENALTY   = 0.18
WOLF_SIZE_HUNGER_SCALE    = 0.40
WOLF_PACKMATE_MATE_BONUS  = 0.35   # prefer mates from the same pack
WOLF_TRUE_LONER_CHANCE    = 0.06
WOLF_MATE_BOND_BONUS      = 3.0
WOLF_MATE_BOND_PULL       = 2.0
WOLF_MATE_SEEK_PULL       = 2.2
WOLF_MATE_SUPPORT_HUNGER  = 0.34
WOLF_MATE_SUPPORT_HP_FRAC = 0.72
WOLF_EAT_STOP_HUNGER      = 0.10    # eat until very full (was 0.14)
WOLF_EAT_STOP_HP_FRAC     = 0.90
WOLF_BETA_DURATION_MIN    = DAY_DURATION * 2.0  # shorter beta punishment (was 3 days)
WOLF_BETA_DURATION_MAX    = DAY_DURATION * 3.0

# Sheep territory avoidance (when not hungry enough to hunt)
WOLF_SHEEP_AVOID_RADIUS   = 80.0   # chill packs should give sheep a very wide berth
WOLF_SHEEP_AVOID_MIN_N    = 1      # even a single nearby sheep matters while not hunting
WOLF_SHEEP_AVOID_FORCE    = 4.5    # strong push away from sheep while not hunting

# Social play (satiated pack members chase and play with each other)
WOLF_PLAY_HUNGER_MAX      = 0.30   # wolf must be below this hunger to play
WOLF_PLAY_CHANCE          = 0.018  # frequent play while satiated — packs should feel alive
WOLF_PLAY_RADIUS          = 18.0   # wider search radius for play partners
WOLF_PLAY_CHASE_DURATION  = 26.0   # longer chase — satisfying to watch
WOLF_PLAY_SUBMIT_DURATION = 9.0    # caught wolf rolls around for a good while
WOLF_PLAY_SPEED_MULT      = 0.85   # speed multiplier during play chase (slower than hunt)
WOLF_IDLE_ROLL_CHANCE     = 0.14   # full wolves flop on their back more often
WOLF_ROLL_MIN             = 4.0
WOLF_ROLL_MAX             = 10.0

# Post-feed lounge (wolves camp near kill site for 1–3 sim-days after a big meal)
WOLF_LOUNGE_HUNGER_TRIGGER = 0.30  # enter lounge when hunger drops below this on EAT exit
WOLF_LOUNGE_MIN            = DAY_DURATION * 1.0   # 300 sim-seconds = 1 day
WOLF_LOUNGE_MAX            = DAY_DURATION * 2.5   # 750 sim-seconds = 2.5 days
WOLF_LOUNGE_IDLE_MIN       = 20.0  # lounge idle period min
WOLF_LOUNGE_IDLE_MAX       = 50.0
WOLF_LOUNGE_WALK_MIN       = 5.0   # short walk bursts
WOLF_LOUNGE_WALK_MAX       = 12.0  # slightly longer exploration bursts
WOLF_LOUNGE_DRIFT_RADIUS   = 14.0  # wolves can wander a bit further from camp
WOLF_LOUNGE_DRIFT_FORCE    = 1.4   # gentler pull-back — let them roam a little
WOLF_LOUNGE_HUNT_HUNGER    = 0.76  # break lounge a bit sooner when hungry (was 0.82)
WOLF_CORPSE_CHEW_DECAY     = 18.0  # slightly slower corpse decay while eating
WOLF_PACK_CHILL_PLAY_BONUS = 14.0  # big play bonus during chill — this is their fun time
WOLF_PACK_DEFENSE_RADIUS   = 22.0


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
        roll_r     = _load("female wolf rolling on back facing right.png")
        roll_l     = pygame.transform.flip(roll_r, True, False)

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
            "roll_right":     roll_r,
            "roll_left":      roll_l,
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
                 genetic_gestation: float = None,
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
        self.wolf_id = Wolf._next_id
        Wolf._next_id += 1
        self.mother_id = mother_id
        self.father_id = father_id
        self.mate_bond_id: int | None = None
        self.preferred_mate_id: int | None = None
        self.beta_timer = 0.0
        self.true_loner = random.random() < WOLF_TRUE_LONER_CHANCE
        self._formation_slot = random.randrange(8)
        self.reproductive_success = 0
        self.mates_count = 0
        self.mate_history_ids: set[int] = set()
        self.wary_of_wolf_ids: set[int] = set()

        # --- Genetics ---
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
        self.genetic_awareness = (
            _cg(genetic_awareness, WOLF_AWARENESS_RANGE) if genetic_awareness is not None
            else random.uniform(1.0 - WOLF_AWARENESS_RANGE, 1.0 + WOLF_AWARENESS_RANGE)
        )
        self.genetic_hp = (
            int(max(WOLF_HP_MIN, min(WOLF_HP_MAX, genetic_hp))) if genetic_hp is not None
            else int(round(max(WOLF_HP_MIN, min(
                WOLF_HP_MAX,
                random.randint(WOLF_HP_MIN, WOLF_HP_MAX)
                + (self.genetic_size - 1.0) * 16.0
                + (self.genetic_strength - 1.0) * 10.0
            ))))
        )
        self.genetic_lifespan = (
            _cg(genetic_lifespan, WOLF_LIFESPAN_RANGE) if genetic_lifespan is not None
            else random.uniform(1.0 - WOLF_LIFESPAN_RANGE, 1.0 + WOLF_LIFESPAN_RANGE)
        )
        self.genetic_gestation = (
            _cg(genetic_gestation, WOLF_GESTATION_RANGE) if genetic_gestation is not None
            else random.uniform(1.0 - WOLF_GESTATION_RANGE, 1.0 + WOLF_GESTATION_RANGE)
        )

        self.genetic_speed = (
            _cg(float(genetic_speed), WOLF_SPEED_RANGE)
            if genetic_speed is not None
            else _cg(random.uniform(1.0 - WOLF_SPEED_RANGE, 1.0 + WOLF_SPEED_RANGE)
                     - (self.genetic_size - 1.0) * 0.28, WOLF_SPEED_RANGE)
        )

        # Derived timing
        self.maturity_age = WOLF_MATURITY_AGE_BASE
        self.lifespan     = (
            random.uniform(WOLF_LIFESPAN_MIN, WOLF_LIFESPAN_MAX) * self.genetic_lifespan
        )
        self.age = float(age) if age is not None else random.uniform(
            self.maturity_age, self.maturity_age * 2.5
        )
        if age is not None and age <= 0.0:
            self.true_loner = False
        self.hp      = float(self.max_hp)
        self.hunger  = random.uniform(0.05, 0.40)

        # Reproduction
        self.pregnant              = False
        self.gestation_timer       = 0.0
        self._pending_litter: list = []
        self.reproduce_cooldown    = random.uniform(0, WOLF_REPRODUCE_COOLDOWN * 0.18)

        # Hunt state
        self._hunt_target          = None    # ref to target Sheep
        self._scan_timer           = random.uniform(0, WOLF_SCAN_INTERVAL)
        self._attack_cooldown      = 0.0
        self._lunge_timer          = 0.0
        self._lunge_active         = False
        self._lunge_phase          = "charge"
        self._lunge_dir_x          = 1.0
        self._lunge_dir_y          = 0.0
        self._meal_cooldown        = 0.0
        self._eat_session_timer    = 0.0
        self._eat_session_meat     = 0.0
        self._corpse_commit_timer  = 0.0
        self._corpse_approach_timer = 0.0
        self._may_eat_kill_id      = None  # id() of corpse this wolf just killed; grants eat bypass
        self._pup_feed_timer       = 0.0   # cooldown between regurgitation feedings

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
        self.pack_mode             = "chill"
        self.pack_mode_timer       = 0.0
        self.pack_alpha_id         = self.wolf_id
        self.pack_is_alpha         = True
        self.pack_rank             = 1
        self.pack_reputation       = 1.0
        self.pack_camp_x           = tile_x
        self.pack_camp_y           = tile_y
        self.pack_blocked_corpse_id = None
        self.pack_move_x           = tile_x
        self.pack_move_y           = tile_y
        self.pack_resting          = False
        self.pack_rest_timer       = 0.0
        self.pack_solo_hunt_ok     = False
        self.pack_memory_x         = float(tile_x)
        self.pack_memory_y         = float(tile_y)
        self.solo_excursion        = False
        self.solo_origin_pack_id   = -1
        self.was_exiled            = False
        self.former_pack_rank      = 1
        self.pair_bond_only        = False
        self.exile_home_x          = float(tile_x)
        self.exile_home_y          = float(tile_y)
        self.exile_home_radius     = 0.0

        # Social play
        self._play_target       = None   # ref to packmate being chased
        self._play_timer        = 0.0    # counts down chase or submit duration
        self._roll_timer        = 0.0

        # Post-feed lounge
        self._lounge_timer      = 0.0
        self._lounge_anchor_x   = float(tile_x)
        self._lounge_anchor_y   = float(tile_y)

        # Pup mortality tracking
        self._pup_death_timer      = 0.0   # countdown for daily pup-mortality check

        # Earned growth (accumulated post-maturity; 0–1 each)
        self.earned_size           = 0.0   # extra bulk from good feeding
        self.earned_strength       = 0.0   # extra power from combat experience

        # Snow exposure — accumulates while on snow; resets when leaving snow
        self.snow_exposure = 0.0

        # Corpse state
        self.alive       = True
        self.dead_state  = None   # None / "fresh" / "decayed"
        self.death_timer = 0.0
        self.death_facing = "right"

        # Pre-filtered neighbor lists written by ProximityScanner each frame
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
        return self.beta_timer > 0.0

    @property
    def size_scale(self) -> float:
        growth = 0.55 + 0.45 * min(1.0, self.age / self.maturity_age)
        size_bonus = 1.0 + getattr(self, "earned_size", 0.0) * WOLF_EARN_SIZE_MAX
        sex_mult = WOLF_MALE_SIZE_MULT if self.sex == "male" else 1.0
        return growth * self.genetic_size * size_bonus * sex_mult * WOLF_BASE_DRAW_SCALE

    @property
    def adult_size_scale(self) -> float:
        sex_mult = WOLF_MALE_SIZE_MULT if self.sex == "male" else 1.0
        size_bonus = 1.0 + getattr(self, "earned_size", 0.0) * WOLF_EARN_SIZE_MAX
        return self.genetic_size * size_bonus * sex_mult

    @property
    def max_hp(self) -> float:
        size_mult = 1.0 + (self.adult_size_scale - 1.0) * WOLF_SIZE_HP_SCALE
        strength_mult = 1.0 + (self.genetic_strength - 1.0) * 0.10
        return max(8.0, float(self.genetic_hp) * size_mult * strength_mult)

    @property
    def move_speed(self) -> float:
        size_penalty = 1.0 - max(-0.18, min(0.22, (self.adult_size_scale - 1.0) * WOLF_SIZE_SPEED_PENALTY))
        base_speed = (WOLF_SPEED_MIN + WOLF_SPEED_MAX) * 0.5
        return max(WOLF_SPEED_MIN * 0.78,
                   min(WOLF_SPEED_MAX * 1.08, base_speed * self.genetic_speed * size_penalty))

    @property
    def appetite_mult(self) -> float:
        return max(0.80, 1.0 + (self.adult_size_scale - 1.0) * WOLF_SIZE_HUNGER_SCALE)

    @property
    def collision_radius(self) -> float:
        return 0.45 + self.size_scale * 0.42

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

    def _set_horizontal_facing_toward(self, tx: float):
        self.facing = "right" if tx >= self.tx else "left"

    def _face_toward_point(self, tx: float, ty: float):
        ddx = tx - self.tx
        ddy = ty - self.ty
        if abs(ddx) >= abs(ddy):
            self.facing = "right" if ddx >= 0 else "left"
        else:
            self.facing = "front" if ddy > 0 else "behind"

    def _separation_delta(self, wolves: list, dt: float) -> tuple:
        sx = sy = 0.0
        for other, _ in self.nearby_wolves:
            if other.dead_state is not None:
                continue
            ddx  = self.tx - other.tx
            ddy  = self.ty - other.ty
            dist = math.sqrt(ddx * ddx + ddy * ddy)
            min_dist = self.collision_radius + other.collision_radius
            if 0 < dist < min_dist:
                strength = (min_dist - dist) / max(0.01, min_dist)
                sx += (ddx / dist) * strength * WOLF_SEPARATION_FORCE * dt
                sy += (ddy / dist) * strength * WOLF_SEPARATION_FORCE * dt
        return sx, sy

    def _target_ring_point(self, target_x: float, target_y: float,
                           radius: float, slot_count: int = 8) -> tuple[float, float]:
        angle = (self._formation_slot % slot_count) * (2 * math.pi / slot_count)
        if self.pack_id >= 0:
            angle += (self.pack_id % slot_count) * 0.18
        return target_x + math.cos(angle) * radius, target_y + math.sin(angle) * radius

    def _corpse_anchor_point(self, corpse) -> tuple[float, float]:
        slots = (
            (0.0, -1.0),  # top
            (0.0,  1.0),  # bottom
            (-1.0, 0.0),  # left
            (1.0,  0.0),  # right
        )
        ox, oy = slots[self._formation_slot % 4]
        return corpse.tx + ox, corpse.ty + oy

    def _corpse_anchor_side(self, corpse) -> str:
        ax, ay = self._corpse_anchor_point(corpse)
        if ay < corpse.ty:
            return "top"
        if ay > corpse.ty:
            return "bottom"
        if ax < corpse.tx:
            return "left"
        return "right"

    def related_to(self, other: "Wolf") -> bool:
        if other is None:
            return False
        if self.wolf_id == other.wolf_id:
            return True
        parents_self = {pid for pid in (self.mother_id, self.father_id) if pid is not None}
        parents_other = {pid for pid in (other.mother_id, other.father_id) if pid is not None}
        if self.wolf_id in parents_other or other.wolf_id in parents_self:
            return True
        return bool(parents_self and parents_self & parents_other)

    def genetic_similarity(self, other: "Wolf") -> float:
        diffs = (
            abs(self.genetic_size - other.genetic_size) / max(0.01, WOLF_SIZE_RANGE * 2),
            abs(self.genetic_strength - other.genetic_strength) / max(0.01, WOLF_STRENGTH_RANGE * 2),
            abs(self.genetic_speed - other.genetic_speed) / max(0.01, WOLF_SPEED_RANGE * 2),
            abs(self.genetic_lifespan - other.genetic_lifespan) / max(0.01, WOLF_LIFESPAN_RANGE * 2),
        )
        return max(0.0, 1.0 - sum(diffs) / len(diffs))

    def _find_wolf_by_id(self, wolf_list: list, wolf_id: int | None):
        if wolf_id is None:
            return None
        for wolf in wolf_list:
            if wolf.wolf_id == wolf_id and wolf.alive and wolf.dead_state is None:
                return wolf
        return None

    def _score_prey(self, sheep) -> float:
        """Higher = better prey target. Prefer weak, young, old."""
        score = 0.0
        if not hasattr(sheep, 'maturity_age'):
            return score
        # Young
        if sheep.age < sheep.maturity_age:
            score += 4.5
        # Old
        elif sheep.age > sheep.lifespan * 0.55:
            score += 3.5
        # Weak HP
        max_hp = float(getattr(sheep, 'max_hp', sheep.genetic_hp))
        if max_hp > 0:
            hp_frac = sheep.hp / max_hp
            if hp_frac < 0.30:
                score += 5.0
            elif hp_frac < 0.60:
                score += 2.5
        if getattr(sheep, 'herd_id', -1) < 0:
            score += 5.0
        else:
            score += max(0.0, 2.5 - min(2.5, getattr(sheep, 'herd_awareness_r', 10.0) * 0.12))
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

        for sheep, _ in self.nearby_sheep:
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
        if self._meal_cooldown > 0.0 or self.pack_mode == "camp":
            return None
        if self.hunger < WOLF_HUNGER_STARVING:
            return None
        smell_sq     = WOLF_SMELL_RADIUS ** 2
        best_dist_sq = float('inf')
        best_corpse  = None

        for sheep, _ in self.nearby_sheep:
            if sheep.dead_state != "fresh":
                continue
            if id(sheep) == self.pack_blocked_corpse_id:
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

    def _corpse_has_open_slot(self, corpse, wolf_list: list) -> bool:
        max_eat = getattr(corpse, "_max_eaters", WOLF_MAX_EATERS_MAX)
        cur_eat = sum(1 for w in wolf_list
                      if w is not self and w.alive and w.dead_state is None
                      and w.state == Wolf.EAT and w._hunt_target is corpse)
        return cur_eat < max_eat

    def _corpse_slot_available(self, corpse, wolf_list: list) -> bool:
        anchor_x, anchor_y = self._corpse_anchor_point(corpse)
        for w in wolf_list:
            if (w is self or not w.alive or w.dead_state is not None
                    or w.state != Wolf.EAT or w._hunt_target is not corpse):
                continue
            wax, way = w._corpse_anchor_point(corpse)
            if abs(wax - anchor_x) < 0.01 and abs(way - anchor_y) < 0.01:
                return False
        return self._corpse_has_open_slot(corpse, wolf_list)

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
        for sheep, _ in self.nearby_sheep:
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
        if self.pack_size <= 1 or self.true_loner:
            return 0.0, 0.0
        ddx  = self.pack_cx - self.tx
        ddy  = self.pack_cy - self.ty
        dist = math.sqrt(ddx * ddx + ddy * ddy)
        if dist <= WOLF_PACK_COHESION_INNER or dist < 0.001:
            return 0.0, 0.0
        # Scale linearly: 0 at inner threshold, full at 3× inner threshold
        t    = min(1.0, (dist - WOLF_PACK_COHESION_INNER) / max(1.0, WOLF_PACK_COHESION_INNER * 2))
        pull = WOLF_PACK_COHESION_FORCE * t * dt
        if self.pack_mode == "chill":
            pull *= 1.65
        if self.state in (Wolf.HUNT, Wolf.LUNGE):
            pull *= 1.35
        return (ddx / dist) * pull, (ddy / dist) * pull

    def _pack_alpha_follow_delta(self, dt: float) -> tuple[float, float]:
        if self.pack_size <= 1 or self.true_loner or self.pack_is_alpha:
            return 0.0, 0.0
        ddx = self.pack_cx - self.tx
        ddy = self.pack_cy - self.ty
        dist = math.sqrt(ddx * ddx + ddy * ddy)
        if dist <= WOLF_PACK_ALPHA_FOLLOW_DIST or dist < 0.001:
            return 0.0, 0.0
        t = min(1.0, (dist - WOLF_PACK_ALPHA_FOLLOW_DIST) / max(1.0, WOLF_PACK_ALPHA_FOLLOW_DIST * 2.5))
        pull = WOLF_PACK_ALPHA_PULL * t * dt
        if self.pack_mode == "chill":
            pull *= 1.35
        return (ddx / dist) * pull, (ddy / dist) * pull

    def _pack_travel_goal(self) -> tuple[float, float]:
        if self.pack_size <= 1 or self.solo_excursion:
            return self.pack_move_x, self.pack_move_y

        goal_x, goal_y = self.pack_move_x, self.pack_move_y
        if self.pack_is_alpha:
            return goal_x, goal_y

        hd_x = goal_x - self.pack_cx
        hd_y = goal_y - self.pack_cy
        hd_d = math.hypot(hd_x, hd_y)
        if hd_d < 0.001:
            ux, uy = 1.0, 0.0
        else:
            ux, uy = hd_x / hd_d, hd_y / hd_d
        px, py = -uy, ux

        slot = max(0, self.pack_rank - 2)
        row = slot // 3
        col = (slot % 3) - 1
        return (
            self.pack_cx - ux * (row + 1) * WOLF_PACK_FORMATION_DEPTH + px * col * WOLF_PACK_FORMATION_SPACING,
            self.pack_cy - uy * (row + 1) * WOLF_PACK_FORMATION_DEPTH + py * col * WOLF_PACK_FORMATION_SPACING,
        )

    def _pack_travel_delta(self, dt: float) -> tuple[float, float]:
        if self.pack_size <= 1 or self.true_loner or self.solo_excursion:
            return 0.0, 0.0
        goal_x, goal_y = self._pack_travel_goal()
        ddx = goal_x - self.tx
        ddy = goal_y - self.ty
        dist = math.hypot(ddx, ddy)
        if dist < 0.001:
            return 0.0, 0.0
        pull = min(1.0, dist / 10.0) * WOLF_PACK_TRAVEL_PULL * dt
        if self.pack_resting:
            pull *= 0.45
        return (ddx / dist) * pull, (ddy / dist) * pull

    def _should_pause_hunger(self) -> bool:
        if self.state == Wolf.EAT:
            return False
        if self.hunger > WOLF_REST_HUNGER_PAUSE_MAX:
            return False
        if self._meal_cooldown <= 0.0:
            return False
        return self.pack_resting or self.state == Wolf.LOUNGE

    def _update_pack_memory(self):
        if self.pack_id >= 0 and not self.solo_excursion:
            self.pack_memory_x = self.pack_cx
            self.pack_memory_y = self.pack_cy
            self.solo_origin_pack_id = self.pack_id

    def _start_solo_excursion(self) -> bool:
        if self.solo_excursion or self.pack_size <= 1 or self.pair_bond_only:
            return False
        self.solo_excursion = True
        self.solo_origin_pack_id = self.pack_id
        self.pack_memory_x = self.pack_cx
        self.pack_memory_y = self.pack_cy
        self.former_pack_rank = max(self.former_pack_rank, self.pack_rank)
        self.pack_id = -1
        self.pack_size = 1
        self.pack_hunt_target = None
        return True

    def _end_solo_excursion_if_home(self) -> bool:
        if not self.solo_excursion:
            return False
        ddx = self.pack_memory_x - self.tx
        ddy = self.pack_memory_y - self.ty
        if math.hypot(ddx, ddy) > WOLF_PACK_RETURN_RADIUS:
            return False
        if self._meal_cooldown <= 0.0 and self.hunger >= WOLF_HUNGER_HUNT:
            return False
        self.solo_excursion = False
        self.state = Wolf.WALK
        self.timer = random.uniform(WOLF_WANDER_INTERVAL_MIN, WOLF_WANDER_INTERVAL_MAX)
        return True

    def _sheep_avoidance_delta(self, sheep_list: list, dt: float) -> tuple:
        """Gently push wolf away from nearby sheep clusters when not hunting."""
        if self.hunger >= WOLF_HUNGER_HUNT and self.pack_mode == "hunt":
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

    def _exile_home_avoidance_delta(self, dt: float) -> tuple[float, float]:
        if self.pack_id >= 0 or not self.was_exiled or self.exile_home_radius <= 0.0:
            return 0.0, 0.0
        ddx = self.tx - self.exile_home_x
        ddy = self.ty - self.exile_home_y
        dist = math.hypot(ddx, ddy)
        if dist >= self.exile_home_radius or dist < 0.001:
            return 0.0, 0.0
        push = (1.0 - dist / self.exile_home_radius) * 5.5 * dt
        return (ddx / dist) * push, (ddy / dist) * push

    def _find_play_partner(self, wolf_list: list):
        """Return a nearby satiated packmate to play with, or None."""
        play_sq = WOLF_PLAY_RADIUS ** 2
        best    = None
        best_d  = float('inf')
        for w, d in self.nearby_wolves:
            if (not w.alive or w.dead_state is not None
                    or w.pack_id != self.pack_id or w.pack_id < 0
                    or w.hunger >= WOLF_PLAY_HUNGER_MAX
                    or w.state in (Wolf.HUNT, Wolf.LUNGE, Wolf.EAT, Wolf.FLEE,
                                   Wolf.PLAY_CHASE, Wolf.PLAY_SUBMIT)):
                continue
            if d <= play_sq and d < best_d:
                best   = w
                best_d = d
        return best

    def _mate_bond_delta(self, wolf_list: list, dt: float) -> tuple[float, float]:
        if self.mate_bond_id is None:
            return 0.0, 0.0
        mate = self._find_wolf_by_id(wolf_list, self.mate_bond_id)
        if mate is None:
            self.mate_bond_id = None
            return 0.0, 0.0
        ddx = mate.tx - self.tx
        ddy = mate.ty - self.ty
        dist = math.sqrt(ddx * ddx + ddy * ddy)
        if dist < 2.2 or dist <= 0.001:
            return 0.0, 0.0
        pull = min(1.0, (dist - 2.2) / 8.0) * WOLF_MATE_BOND_PULL * dt
        return (ddx / dist) * pull, (ddy / dist) * pull

    def _mate_seek_delta(self, wolf_list: list, dt: float) -> tuple[float, float]:
        if not self.is_adult or self.hunger >= WOLF_HUNGER_HUNT:
            return 0.0, 0.0

        target = None
        best_score = -1e9

        if self.sex == "male" and not self.is_beta:
            for other, dist_sq in self.nearby_wolves:
                if (not other.alive or other.dead_state is not None
                        or not other.is_adult or other.sex != "female"
                        or other.pregnant):
                    continue
                limit = 18.0 if (self.pack_id >= 0 and other.pack_id == self.pack_id) else 36.0
                if dist_sq > limit * limit:
                    continue
                ddx = other.tx - self.tx
                ddy = other.ty - self.ty
                dist = math.sqrt(dist_sq)
                score = 1.4 - dist / max(1.0, limit)
                if self.mate_bond_id == other.wolf_id:
                    score += WOLF_MATE_BOND_BONUS
                elif self.pack_id >= 0 and other.pack_id == self.pack_id:
                    score += 0.7
                elif self.pack_id < 0 and other.pack_id < 0:
                    score += 1.0
                if score > best_score:
                    best_score = score
                    target = other

        elif self.sex == "female" and not self.pregnant:
            mate = self._find_wolf_by_id(wolf_list, self.mate_bond_id)
            if mate is not None and mate.is_adult and mate.sex == "male" and not mate.is_beta:
                target = mate
            else:
                for other, dist_sq in self.nearby_wolves:
                    if (not other.alive or other.dead_state is not None
                            or not other.is_adult or other.sex != "male"
                            or other.is_beta):
                        continue
                    limit = 18.0 if (self.pack_id >= 0 and other.pack_id == self.pack_id) else 32.0
                    if dist_sq > limit * limit:
                        continue
                    ddx = other.tx - self.tx
                    ddy = other.ty - self.ty
                    dist = math.sqrt(dist_sq)
                    score = 1.2 - dist / max(1.0, limit)
                    if self.preferred_mate_id == other.wolf_id:
                        score += WOLF_MATE_BOND_BONUS * 1.2
                    if self.pack_id >= 0 and other.pack_id == self.pack_id:
                        score += 0.9
                    if other.hunger < WOLF_REPRODUCE_HUNGER:
                        score += 0.5
                    if score > best_score:
                        best_score = score
                        target = other

        if target is None:
            return 0.0, 0.0

        ddx = target.tx - self.tx
        ddy = target.ty - self.ty
        dist = math.sqrt(ddx * ddx + ddy * ddy)
        if dist <= 0.001 or dist < 1.5:
            return 0.0, 0.0
        pull = min(1.0, dist / 10.0) * WOLF_MATE_SEEK_PULL * dt
        return (ddx / dist) * pull, (ddy / dist) * pull

    def _mate_hunt_support_target(self, wolf_list: list):
        support_limit = 0.58 if self.pair_bond_only else WOLF_MATE_SUPPORT_HUNGER
        if self.mate_bond_id is None or self.hunger > support_limit:
            return None
        mate = self._find_wolf_by_id(wolf_list, self.mate_bond_id)
        if mate is None or not mate.is_adult:
            return None
        mate_hp_frac = mate.hp / max(1.0, mate.max_hp)
        mate_needs_help = (
            mate.hunger >= WOLF_HUNGER_HUNT
            or mate_hp_frac <= WOLF_MATE_SUPPORT_HP_FRAC
            or mate.state in (Wolf.HUNT, Wolf.LUNGE, Wolf.FLEE)
        )
        if not mate_needs_help:
            return None
        target = getattr(mate, "_hunt_target", None)
        if target is not None and getattr(target, "alive", False) and getattr(target, "dead_state", None) is None:
            return target
        target = getattr(mate, "pack_hunt_target", None)
        if target is not None and getattr(target, "alive", False) and getattr(target, "dead_state", None) is None:
            return target
        return True

    def _pack_defense_target(self, sheep_list: list):
        if self.pack_mode == "hunt":
            return None
        defense_sq = WOLF_PACK_DEFENSE_RADIUS ** 2
        best = None
        best_dist = float("inf")
        for sheep in sheep_list:
            if sheep.dead_state is not None or not sheep.alive:
                continue
            ddx = sheep.tx - self.pack_cx
            ddy = sheep.ty - self.pack_cy
            dist_sq = ddx * ddx + ddy * ddy
            if dist_sq > defense_sq:
                continue
            if dist_sq < best_dist:
                best = sheep
                best_dist = dist_sq
        return best

    def _enter_lounge(self):
        """Trigger post-feed lounge if wolf is very satiated."""
        if self.hunger <= WOLF_LOUNGE_HUNGER_TRIGGER and self._lounge_timer <= 0:
            if self.pack_mode == "camp":
                self._lounge_timer = max(self.pack_mode_timer, random.uniform(WOLF_MEAL_COOLDOWN_MIN,
                                                                              WOLF_MEAL_COOLDOWN_MAX))
                self._lounge_anchor_x = self.pack_camp_x
                self._lounge_anchor_y = self.pack_camp_y
                return
            if self.pack_mode == "chill":
                self._lounge_timer = max(self.pack_mode_timer, random.uniform(WOLF_LOUNGE_MIN, WOLF_LOUNGE_MAX))
            else:
                self._lounge_timer = random.uniform(WOLF_LOUNGE_MIN, WOLF_LOUNGE_MAX)
            self._lounge_anchor_x = self.tx
            self._lounge_anchor_y = self.ty

    def _feed_nearby_pups(self, wolf_list: list):
        """Satiated adult regurgitates food for nearby pack pups during camp/chill."""
        if (not self.is_adult
                or self._pup_feed_timer > 0.0
                or self._meal_cooldown < WOLF_PUP_FEED_MIN_COOLDOWN):
            return
        feed_sq = WOLF_PUP_FEED_RADIUS ** 2
        for w in wolf_list:
            if (w is self or not w.alive or w.dead_state is not None
                    or w.is_adult or w.pack_id != self.pack_id
                    or w.hunger <= 0.05):
                continue
            ddx = w.tx - self.tx
            ddy = w.ty - self.ty
            if ddx * ddx + ddy * ddy <= feed_sq:
                w.hunger = max(0.0, w.hunger - WOLF_PUP_FEED_AMOUNT)
                self._pup_feed_timer = WOLF_PUP_FEED_INTERVAL
                return  # feed one pup per interval

    def _start_eating(self, corpse):
        self.state = Wolf.EAT
        self._hunt_target = corpse
        self._eat_session_timer = 0.0
        self._eat_session_meat = 0.0
        self._corpse_approach_timer = 0.0
        self._may_eat_kill_id = None  # used up once eating begins

    def _finish_eating(self):
        had_meal = self._eat_session_timer > 0.0 and self._eat_session_meat > 0.0
        self._hunt_target = None
        if had_meal:
            self.hunger = min(self.hunger, 0.03)
            self._meal_cooldown = max(
                self._meal_cooldown,
                random.uniform(WOLF_MEAL_COOLDOWN_MIN, WOLF_MEAL_COOLDOWN_MAX),
            )
            self._lounge_timer = max(self._lounge_timer, self._meal_cooldown)
            self._lounge_anchor_x = self.tx
            self._lounge_anchor_y = self.ty
        self._eat_session_timer = 0.0
        self._eat_session_meat = 0.0
        self._corpse_commit_timer = 0.0
        self._corpse_approach_timer = 0.0
        self._enter_lounge()
        if self.solo_excursion:
            self.state = Wolf.WALK
        elif self._lounge_timer > 0:
            self.state = Wolf.LOUNGE
        else:
            self.state = Wolf.IDLE
        self.timer = random.uniform(WOLF_WANDER_INTERVAL_MIN, WOLF_WANDER_INTERVAL_MAX)

    def _abandon_corpse(self):
        self._hunt_target = None
        self._eat_session_timer = 0.0
        self._eat_session_meat = 0.0
        self._corpse_commit_timer = 0.0
        self._corpse_approach_timer = 0.0
        self.state = Wolf.LOUNGE if self._lounge_timer > 0 else Wolf.WALK
        self.timer = random.uniform(WOLF_WANDER_INTERVAL_MIN, WOLF_WANDER_INTERVAL_MAX)

    def _begin_lunge(self, target):
        ddx = target.tx - self.tx
        ddy = target.ty - self.ty
        dist = math.sqrt(ddx * ddx + ddy * ddy)
        if dist < 0.001:
            self._lunge_dir_x, self._lunge_dir_y = 1.0, 0.0
        else:
            self._lunge_dir_x = ddx / dist
            self._lunge_dir_y = ddy / dist
        self._set_horizontal_facing_toward(target.tx)
        self.state = Wolf.LUNGE
        self._lunge_timer = WOLF_LUNGE_DURATION
        self._lunge_phase = "charge"
        self._lunge_active = True

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
            self._may_eat_kill_id = id(target)  # killer gets first-bite bypass
            return True
        return False

    def _ram_guard_pressure(self, target, sheep_list: list) -> float:
        from ram import Ram
        if target is None:
            return 0.0
        pressure = 0.0
        guard_sq = 4.0 * 4.0
        for sheep in sheep_list:
            if (sheep is target or not isinstance(sheep, Ram) or not sheep.alive
                    or sheep.dead_state is not None or not sheep.is_adult):
                continue
            ddx = sheep.tx - target.tx
            ddy = sheep.ty - target.ty
            if ddx * ddx + ddy * ddy <= guard_sq:
                pressure += 1.0 + sheep.genetic_strength * 0.35
        if getattr(target, 'age', 999999) < getattr(target, 'maturity_age', 0.0):
            pressure *= 1.35
        if self.hp < self.max_hp * 0.75:
            pressure *= 1.25
        return pressure

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
        best_mate = None
        best_score = -1.0
        for other, dist_sq in self.nearby_wolves:
            if (other.dead_state is not None or not other.alive
                    or not other.is_adult or other.sex != "male"
                    or other.reproduce_cooldown > 0
                    or other.hunger >= WOLF_REPRODUCE_HUNGER
                    or other.is_beta):
                continue
            if self.related_to(other):
                continue
            if dist_sq > mate_sq:
                continue
            ddx = other.tx - self.tx
            ddy = other.ty - self.ty
            score = (1.0 - other.hunger) + (1.0 - abs(other.tx - self.tx) / max(1.0, WOLF_MATE_RADIUS))
            if self.mate_bond_id == other.wolf_id:
                score += WOLF_MATE_BOND_BONUS
            if self.preferred_mate_id == other.wolf_id:
                score += WOLF_MATE_BOND_BONUS * 1.25
            if self.pack_id >= 0 and other.pack_id == self.pack_id:
                score += WOLF_PACKMATE_MATE_BONUS
            elif self.pack_id >= 0 or other.pack_id >= 0:
                score -= 0.15
            if score > best_score:
                best_score = score
                best_mate = other

        if best_mate is None:
            return

        other = best_mate
        self.mate_bond_id = other.wolf_id
        other.mate_bond_id = self.wolf_id
        self.preferred_mate_id = other.wolf_id
        if self.wolf_id not in other.mate_history_ids:
            other.mate_history_ids.add(self.wolf_id)
            other.mates_count += 1
        if other.wolf_id not in self.mate_history_ids:
            self.mate_history_ids.add(other.wolf_id)
            self.mates_count += 1

        def _inherit(a, b, attr, r):
            mid = (getattr(a, attr) + getattr(b, attr)) / 2.0
            return max(1.0 - r, min(1.0 + r, mid + random.gauss(0, r * 0.12)))

        raw_litter = random.randint(WOLF_LITTER_MIN, WOLF_LITTER_MAX)
        survivors  = sum(1 for _ in range(raw_litter)
                         if random.random() > WOLF_PUP_MORTALITY)
        litter_count = max(1, survivors)

        pending = []
        for _ in range(litter_count):
            baby_size = _inherit(self, other, "genetic_size", WOLF_SIZE_RANGE)
            baby_strength = _inherit(self, other, "genetic_strength", WOLF_STRENGTH_RANGE)
            pending.append({
                "sex":       "male" if random.random() < 0.5 else "female",
                "size":      baby_size,
                "strength":  baby_strength,
                "aware":     _inherit(self, other, "genetic_awareness", WOLF_AWARENESS_RANGE),
                "hp":        int(round(max(WOLF_HP_MIN, min(
                                WOLF_HP_MAX,
                                (self.genetic_hp + other.genetic_hp) / 2.0
                                + (baby_size - 1.0) * 10.0
                                + (baby_strength - 1.0) * 6.0
                                + random.gauss(0, 1.2)
                            )))),
                "lifespan":  _inherit(self, other, "genetic_lifespan", WOLF_LIFESPAN_RANGE),
                "gestation": _inherit(self, other, "genetic_gestation", WOLF_GESTATION_RANGE),
                "speed":     _inherit(self, other, "genetic_speed", WOLF_SPEED_RANGE),
            })

        self.pregnant        = True
        self.gestation_timer = ((WOLF_GESTATION_BASE
                                 + WOLF_GESTATION_PER_CUB * max(0, litter_count - WOLF_LITTER_MIN))
                                * self.genetic_gestation)
        self._pending_litter = pending
        self.reproductive_success += litter_count
        other.reproductive_success += litter_count
        self.reproduce_cooldown  = WOLF_REPRODUCE_COOLDOWN * 0.65
        other.reproduce_cooldown = WOLF_REPRODUCE_COOLDOWN * 0.45
        self.hunger  = min(1.0, self.hunger + 0.04 * litter_count)
        other.hunger = min(1.0, other.hunger + 0.02 * litter_count)

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
                if is_walkable_tile(grid, r, c):
                    pup = Wolf(ox, oy,
                               age=0.0,
                               sex=data["sex"],
                               genetic_size=data["size"],
                               genetic_strength=data["strength"],
                               genetic_awareness=data["aware"],
                               genetic_hp=data["hp"],
                               genetic_lifespan=data["lifespan"],
                               genetic_gestation=data["gestation"],
                               genetic_speed=data["speed"],
                               mother_id=self.wolf_id,
                               father_id=self.mate_bond_id)
                    pup.hunger  = 0.0
                    pup.pack_id = self.pack_id
                    pup.mate_bond_id = None
                    pup.pair_bond_only = self.pair_bond_only
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
        gx, gy = self._pack_travel_delta(dt)
        spd    = self.move_speed * speed_mult
        new_tx = self.tx + self.dx * spd * dt + sx + px + gx
        new_ty = self.ty + self.dy * spd * dt + sy + py + gy

        self.tx, self.ty, _ = advance_until_blocked(grid, self.tx, self.ty, new_tx, new_ty)

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
        if self._meal_cooldown > 0:
            self._meal_cooldown = max(0.0, self._meal_cooldown - dt)
        if self._scan_timer > 0:
            self._scan_timer = max(0.0, self._scan_timer - dt)
        if self.reproduce_cooldown > 0:
            self.reproduce_cooldown = max(0.0, self.reproduce_cooldown - dt)
        if self.beta_timer > 0:
            self.beta_timer = max(0.0, self.beta_timer - dt)
        if self._play_timer > 0:
            self._play_timer = max(0.0, self._play_timer - dt)
        if self._roll_timer > 0:
            self._roll_timer = max(0.0, self._roll_timer - dt)
        if self._lounge_timer > 0:
            self._lounge_timer = max(0.0, self._lounge_timer - dt)
        if self._pup_feed_timer > 0:
            self._pup_feed_timer = max(0.0, self._pup_feed_timer - dt)
        self._update_pack_memory()
        self._end_solo_excursion_if_home()

        # Pup daily mortality check
        if not self.is_adult:
            self._pup_death_timer -= dt
            if self._pup_death_timer <= 0:
                self._pup_death_timer = DAY_DURATION
                if random.random() < WOLF_PUP_DEATH_DAILY:
                    self._die()
                    return

        # --- Hunger & HP ---
        if self.state != Wolf.EAT and not self._should_pause_hunger():
            self.hunger = min(1.0, self.hunger + WOLF_HUNGER_RATE * self.appetite_mult * dt)
        if self.hunger >= 1.0:
            self.hp = max(0.0, self.hp - WOLF_HP_DRAIN_RATE * dt)
        # Idle regen when satiated
        if self.state in (Wolf.IDLE, Wolf.LOUNGE, Wolf.PLAY_SUBMIT) and self.hunger < 0.45:
            self.hp = min(self.max_hp, self.hp + WOLF_HP_REGEN_RATE * dt)

        # --- Snow exposure damage ---
        _rows = len(grid)
        _cols = len(grid[0]) if _rows else 0
        _wr, _wc = int(self.ty), int(self.tx)
        _on_snow = (0 <= _wr < _rows and 0 <= _wc < _cols
                    and grid[_wr][_wc] == SNOW)
        if _on_snow:
            self.snow_exposure += dt
            if self.snow_exposure >= WOLF_SNOW_EXPOSURE_THRESHOLD:
                self.hp = max(0.0, self.hp - WOLF_SNOW_DAMAGE_RATE * dt)
        else:
            self.snow_exposure = 0.0

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
                if (t.dead_state == "fresh"
                        and getattr(t, 'meat_value', 0) > 0
                        and self.pack_mode == "feast"):
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
            new_tx = self.tx + self.dx * self.move_speed * 1.4 * dt + sx
            new_ty = self.ty + self.dy * self.move_speed * 1.4 * dt + sy
            self.tx, self.ty, _ = advance_until_blocked(grid, self.tx, self.ty, new_tx, new_ty)
            # Stop fleeing when timer up AND HP partly recovered
            if self._flee_timer <= 0 and self.hp >= self.max_hp * 0.45:
                self.state = Wolf.IDLE
                self.timer = random.uniform(WOLF_WANDER_INTERVAL_MIN, WOLF_WANDER_INTERVAL_MAX)
            return

        # ── LOUNGE ──────────────────────────────────────────────────────
        if self.state == Wolf.LOUNGE:
            if self.pack_mode == "camp":
                self._lounge_anchor_x = self.pack_camp_x
                self._lounge_anchor_y = self.pack_camp_y
            elif self.pack_size > 1 and not self.solo_excursion:
                self._lounge_anchor_x = self.pack_cx
                self._lounge_anchor_y = self.pack_cy
            # Break lounge if desperately hungry
            if self.hunger >= WOLF_LOUNGE_HUNT_HUNGER and self.pack_mode == "hunt":
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

                # Feed nearby pups while lounging in camp/chill
                if self.pack_mode in ("camp", "chill"):
                    self._feed_nearby_pups(wolf_list)

                # Idle/walk oscillation with lounge biases
                if self.timer <= 0:
                    # Opportunity to play while lounging (satiated)
                    if (self.hunger < WOLF_PLAY_HUNGER_MAX and self.pack_size > 1
                            and random.random() < WOLF_PLAY_CHANCE * dt * 60.0
                            * (WOLF_PACK_CHILL_PLAY_BONUS if self.pack_mode == "chill" else 1.0)):
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
                    fx, fy = self._pack_alpha_follow_delta(dt)
                    gx, gy = self._pack_travel_delta(dt)
                    new_tx = self.tx + self.dx * self.move_speed * 0.32 * dt + sx + px + fx + gx
                    new_ty = self.ty + self.dy * self.move_speed * 0.32 * dt + sy + py + fy + gy
                    old_tx, old_ty = self.tx, self.ty
                    self.tx, self.ty, blocked = advance_until_blocked(
                        grid, self.tx, self.ty, new_tx, new_ty
                    )
                    if blocked and abs(self.tx - old_tx) < 1e-6 and abs(self.ty - old_ty) < 1e-6:
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
            self._roll_timer = max(self._roll_timer, min(self._play_timer, WOLF_PLAY_SUBMIT_DURATION))
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
            if ((self.pack_size > 1 and self.pack_mode != "feast")
                    or id(self._hunt_target) == self.pack_blocked_corpse_id):
                self._finish_eating()
                return
            corpse = self._hunt_target
            if (corpse is None or corpse.dead_state != "fresh"
                    or getattr(corpse, 'meat_value', 0) <= 0):
                # Corpse gone or depleted — done eating
                self._finish_eating()
                return

            anchor_x, anchor_y = self._corpse_anchor_point(corpse)
            ddx = anchor_x - self.tx
            ddy = anchor_y - self.ty
            dist_anchor = math.sqrt(ddx * ddx + ddy * ddy)
            if dist_anchor > max(0.18, self.collision_radius * 0.30):
                self._corpse_commit_timer += dt
                self._corpse_approach_timer += dt
                if (self._corpse_commit_timer >= WOLF_CORPSE_COMMIT_MAX
                        or self._corpse_approach_timer >= WOLF_CORPSE_APPROACH_MAX):
                    self._abandon_corpse()
                    return
                self._move_toward(anchor_x, anchor_y, dt, grid, wolf_list, speed_mult=0.55)
                return

            self._corpse_approach_timer = 0.0

            side = self._corpse_anchor_side(corpse)
            if side == "left":
                self.facing = "right"
            elif side == "right":
                self.facing = "left"
            elif side == "top":
                self.facing = "front"
            else:
                self.facing = "behind"

            # Consume meat
            self._eat_session_timer += dt
            self._corpse_commit_timer += dt
            bite      = WOLF_EAT_RATE * dt
            available = corpse.meat_value
            consumed  = min(bite, available)
            corpse.meat_value -= consumed
            self._eat_session_meat += consumed
            self._roll_timer = 0.0

            self.hunger = max(0.0, self.hunger - consumed * WOLF_HUNGER_PER_MEAT)
            self.hp     = min(self.max_hp, self.hp + WOLF_EAT_REGEN * dt)
            corpse.corpse_decay_rate = (
                getattr(corpse, "corpse_decay_rate", 1.0) + WOLF_CORPSE_CHEW_DECAY
            )

            if (self._eat_session_timer >= WOLF_EAT_MAX_SESSION
                    or self._corpse_commit_timer >= WOLF_CORPSE_COMMIT_MAX
                    or (self.hunger <= WOLF_EAT_STOP_HUNGER
                        and self.hp >= self.max_hp * WOLF_EAT_STOP_HP_FRAC)):
                self._finish_eating()
                return

            # When meat depleted, fast-track corpse to decayed
            if corpse.meat_value <= 0 or corpse.death_timer <= 0:
                corpse.dead_state  = "decayed"
                corpse.death_timer = random.uniform(30.0, 90.0)   # decays quickly
                self._finish_eating()
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
            self.facing = "right" if self._lunge_dir_x >= 0 else "left"
            if self._lunge_phase == "charge":
                stop_x = target.tx - self._lunge_dir_x * WOLF_LUNGE_STOP_DIST
                stop_y = target.ty - self._lunge_dir_y * WOLF_LUNGE_STOP_DIST
                self._move_toward(stop_x, stop_y, dt, grid, wolf_list,
                                  speed_mult=WOLF_LUNGE_SPEED / max(self.move_speed, 0.1))

                if self._lunge_timer <= 0:
                    killed = self._do_lunge_damage(target)
                    self._attack_cooldown = WOLF_ATTACK_COOLDOWN
                    self._lunge_phase = "recover"
                    self._lunge_timer = WOLF_LUNGE_RECOVER_TIME

                    if self.is_adult and self.earned_strength < 1.0:
                        self.earned_strength = min(
                            1.0, self.earned_strength + WOLF_EARN_STR_PER_LUNGE)

                    if killed:
                        target._max_eaters = 4
                return

            sx, sy = self._separation_delta(wolf_list, dt)
            back_x = self.tx - self._lunge_dir_x * WOLF_LUNGE_RECOVER_SPEED * dt + sx
            back_y = self.ty - self._lunge_dir_y * WOLF_LUNGE_RECOVER_SPEED * dt + sy
            self.tx, self.ty, _ = advance_until_blocked(grid, self.tx, self.ty, back_x, back_y)

            if self._lunge_timer <= 0:
                self._lunge_active = False
                if self.hp < self.max_hp * WOLF_FLEE_HP_FRAC:
                    self.state       = Wolf.FLEE
                    self._flee_timer = 25.0
                    self._flee_cx    = target.tx
                    self._flee_cy    = target.ty
                    self._hunt_target = None
                    return
                self.state = Wolf.HUNT
            return

        # ── HUNT ────────────────────────────────────────────────────────
        if self.state == Wolf.HUNT:
            # Check if we should flee first
            if self.hp < self.max_hp * WOLF_FLEE_HP_FRAC:
                tx_flee = self._hunt_target.tx if self._hunt_target else self.tx
                ty_flee = self._hunt_target.ty if self._hunt_target else self.ty
                self.state       = Wolf.FLEE
                self._flee_timer = 25.0
                self._flee_cx    = tx_flee
                self._flee_cy    = ty_flee
                self._hunt_target = None
                return

            # Pack cohesion enforcement — too far from packmates, return first
            if self.solo_excursion:
                ddx_home = self.tx - self.pack_memory_x
                ddy_home = self.ty - self.pack_memory_y
                if math.hypot(ddx_home, ddy_home) > WOLF_PACK_VENTURE_RADIUS and self._hunt_target is None:
                    self.state = Wolf.WALK
                    self.timer = random.uniform(WOLF_WANDER_INTERVAL_MIN, WOLF_WANDER_INTERVAL_MAX)
                    return
            elif self.pack_size > 1 and not self.true_loner:
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
                can_feed_corpse = (
                    self.pack_mode == "feast"
                    or id(target) == self._may_eat_kill_id   # killer gets first bite
                    or (self.pack_size <= 1 and self.hunger >= WOLF_HUNGER_STARVING)
                )
                if (self._meal_cooldown > 0.0
                        or not can_feed_corpse
                        or id(target) == self.pack_blocked_corpse_id):
                    self._hunt_target = None
                    self.state = Wolf.LOUNGE if self._lounge_timer > 0 else Wolf.WALK
                    self.timer = random.uniform(WOLF_WANDER_INTERVAL_MIN,
                                                WOLF_WANDER_INTERVAL_MAX)
                    return
                self._corpse_commit_timer += dt
                # Scare any rival pack feeding on this corpse
                self._scare_rival_pack_at_corpse(target, wolf_list)
                anchor_x, anchor_y = self._corpse_anchor_point(target)
                ddx_a = anchor_x - self.tx
                ddy_a = anchor_y - self.ty
                dist_anchor = math.sqrt(ddx_a * ddx_a + ddy_a * ddy_a)
                if dist_anchor > max(0.24, self.collision_radius * 0.35):
                    self._corpse_approach_timer += dt
                else:
                    self._corpse_approach_timer = 0.0
                if (self._corpse_commit_timer >= WOLF_CORPSE_COMMIT_MAX
                        or self._corpse_approach_timer >= WOLF_CORPSE_APPROACH_MAX):
                    self._abandon_corpse()
                    return
                if (dist_anchor < max(0.24, self.collision_radius * 0.35)
                        and getattr(target, 'meat_value', 0) > 0):
                    max_eat = getattr(target, '_max_eaters', WOLF_MAX_EATERS_MAX)
                    cur_eat = sum(1 for w in wolf_list
                                  if w is not self and w.alive
                                  and w.dead_state is None
                                  and w.state == Wolf.EAT
                                  and w._hunt_target is target)
                    if cur_eat < max_eat and self._corpse_slot_available(target, wolf_list):
                        self._start_eating(target)
                    elif (self.pack_hunt_target is not None and self.pack_hunt_target.alive
                          and self.pack_hunt_target.dead_state is None):
                        self._hunt_target = self.pack_hunt_target
                        self.state = Wolf.HUNT
                    return
                self._move_toward(anchor_x, anchor_y, dt, grid, wolf_list)
                return
            else:
                self._corpse_commit_timer = 0.0
                self._corpse_approach_timer = 0.0

            # Periodically rescan for a better target
            if self._scan_timer <= 0:
                self._scan_timer = WOLF_SCAN_INTERVAL
                corpse = self._find_nearest_corpse(sheep_list, wolf_list)
                prefer_live_pack_hunt = (not self.solo_excursion
                                         and self.pack_size > 1 and not self.true_loner
                                         and self.pack_hunt_target is not None
                                         and self.pack_hunt_target.alive
                                         and self.pack_hunt_target.dead_state is None)
                corpse_close = False
                if corpse is not None:
                    ddx_c = corpse.tx - self.tx
                    ddy_c = corpse.ty - self.ty
                    corpse_close = ddx_c * ddx_c + ddy_c * ddy_c <= 12.0 * 12.0
                if (corpse is not None
                        and self._corpse_has_open_slot(corpse, wolf_list)
                        and (not prefer_live_pack_hunt or corpse_close or self.hunger >= WOLF_HUNGER_STARVING)):
                    self._hunt_target = corpse
                    return
                # Follow the pack's shared live target when possible
                pack_t = self.pack_hunt_target
                if (not self.solo_excursion and pack_t is not None and pack_t.alive
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

            guard_pressure = self._ram_guard_pressure(target, sheep_list)
            if guard_pressure >= 2.2 and self.hp < self.max_hp * 0.90:
                self.state       = Wolf.FLEE
                self._flee_timer = 18.0
                self._flee_cx    = target.tx
                self._flee_cy    = target.ty
                self._hunt_target = None
                return

            # Move toward prey
            ddx  = target.tx - self.tx
            ddy  = target.ty - self.ty
            dist = math.sqrt(ddx * ddx + ddy * ddy)

            attack_dist = WOLF_ATTACK_RANGE + self.collision_radius * 0.15
            if dist <= attack_dist and self._attack_cooldown <= 0:
                self._begin_lunge(target)
                return

            if self.pack_size > 1 and not self.true_loner and not self.solo_excursion:
                ring_r = max(WOLF_LUNGE_STOP_DIST + 0.45, self.collision_radius + 0.55)
                hunt_x, hunt_y = self._target_ring_point(target.tx, target.ty, ring_r)
                self._move_toward(hunt_x, hunt_y, dt, grid, wolf_list)
            else:
                self._move_toward(target.tx, target.ty, dt, grid, wolf_list)
            return

        # ── IDLE / WALK ──────────────────────────────────────────────────

        # Should we start hunting?
        # Wolves in camp can still forage if they missed the feast and are getting hungry
        can_seek_food = self._meal_cooldown <= 0.0 and (
            self.solo_excursion or self.pack_mode != "camp" or self.hunger >= 0.62
        )
        if (not self.solo_excursion and can_seek_food and self.pack_solo_hunt_ok
                and self._hunt_target is None):
            self._start_solo_excursion()
            can_seek_food = self._meal_cooldown <= 0.0

        mate_support = self._mate_hunt_support_target(wolf_list) if ((self.pack_mode == "hunt" or self.pair_bond_only) and can_seek_food) else None
        defense_target = self._pack_defense_target(sheep_list) if self.pack_mode != "camp" else None
        should_hunt = (
            defense_target is not None or
            (can_seek_food and (
                (self.solo_excursion and self.hunger >= WOLF_HUNGER_HUNT) or
                self.hunger >= WOLF_HUNGER_DESPERATE or
                (self.pack_mode == "hunt" and (
                    self.hunger >= WOLF_HUNGER_HUNT or
                    mate_support is not None or
                    (self.pack_hunt_target is not None
                     and self.pack_hunt_target.alive
                     and self.pack_hunt_target.dead_state is None)
                ))
            ))
        )
        # Corpses always take priority — check them first (smell range 1000 tiles)
        if can_seek_food and self.pack_mode != "chill" and self._scan_timer <= 0:
            corpse = self._find_nearest_corpse(sheep_list, wolf_list)
            prefer_live_pack_hunt = (not self.solo_excursion
                                     and self.pack_size > 1 and not self.true_loner
                                     and self.pack_hunt_target is not None
                                     and self.pack_hunt_target.alive
                                     and self.pack_hunt_target.dead_state is None)
            corpse_close = False
            if corpse is not None:
                ddx_c = corpse.tx - self.tx
                ddy_c = corpse.ty - self.ty
                corpse_close = ddx_c * ddx_c + ddy_c * ddy_c <= 12.0 * 12.0
            if (corpse is not None
                    and self._corpse_has_open_slot(corpse, wolf_list)
                    and (not prefer_live_pack_hunt or corpse_close or self.hunger >= 0.82)):
                self._hunt_target = corpse
                self.state        = Wolf.HUNT
                self._scan_timer  = WOLF_SCAN_INTERVAL
                return

        if should_hunt and self._scan_timer <= 0:
            self._scan_timer = WOLF_SCAN_INTERVAL
            target = None
            if defense_target is not None:
                target = defense_target
            if target is None and mate_support not in (None, True):
                target = mate_support
            if (target is None and self.pack_hunt_target is not None
                    and not self.solo_excursion
                    and self.pack_hunt_target.alive
                    and self.pack_hunt_target.dead_state is None):
                target = self.pack_hunt_target
            if target is None:
                target = self._find_best_prey(sheep_list)
            if target is not None:
                self._hunt_target = target
                self.state        = Wolf.HUNT
                return

        # Reproduction — allowed during chill AND camp (post-feast rest is prime breeding time)
        if (self.pack_mode in ("chill", "camp")
                and self.is_adult and self.sex == "female"
                and not self.pregnant and self.reproduce_cooldown <= 0
                and self.hunger < WOLF_REPRODUCE_HUNGER):
            self._try_reproduce(wolf_list, new_wolves)

        # Social play: satiated idle wolf may initiate a chase with a packmate
        if (self.state == Wolf.IDLE and self.pack_size > 1
                and self.hunger < WOLF_PLAY_HUNGER_MAX
                and random.random() < WOLF_PLAY_CHANCE * dt
                * (WOLF_PACK_CHILL_PLAY_BONUS if self.pack_mode == "chill" else 1.0)):
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
                if self.solo_excursion and (self._meal_cooldown > 0.0 or self.hunger < WOLF_HUNGER_HUNT):
                    ddx = self.pack_memory_x - self.tx
                    ddy = self.pack_memory_y - self.ty
                    dist = math.hypot(ddx, ddy)
                    if dist > 0.001:
                        self.dx = ddx / dist
                        self.dy = ddy / dist
                    else:
                        self.dx = self.dy = 0.0
                elif self.pack_size > 1 and not self.solo_excursion:
                    goal_x, goal_y = self._pack_travel_goal()
                    ddx = goal_x - self.tx
                    ddy = goal_y - self.ty
                    dist = math.hypot(ddx, ddy)
                    if dist > 0.001:
                        self.dx = ddx / dist
                        self.dy = ddy / dist
                    else:
                        self.dx = self.dy = 0.0
                else:
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
            hx, hy = self._exile_home_avoidance_delta(dt)
            px, py = self._pack_cohesion_delta(dt)
            fx, fy = self._pack_alpha_follow_delta(dt)
            gx, gy = self._pack_travel_delta(dt)
            mx, my = self._mate_bond_delta(wolf_list, dt)
            sx += mx
            sy += my
            qx, qy = self._mate_seek_delta(wolf_list, dt)
            ax += qx
            ay += qy
            ax += hx
            ay += hy
            if self.solo_excursion and (self._meal_cooldown > 0.0 or self.hunger < WOLF_HUNGER_HUNT):
                ddx = self.pack_memory_x - self.tx
                ddy = self.pack_memory_y - self.ty
                dist = math.hypot(ddx, ddy)
                if dist > 0.001:
                    self.dx = ddx / dist
                    self.dy = ddy / dist
            elif self.pack_size > 1 and not self.solo_excursion:
                goal_x, goal_y = self._pack_travel_goal()
                ddx = goal_x - self.tx
                ddy = goal_y - self.ty
                dist = math.hypot(ddx, ddy)
                if dist > 0.001:
                    self.dx = ddx / dist
                    self.dy = ddy / dist
                    self._refresh_facing()
            self._roll_timer = 0.0
            drift_speed = self.move_speed * (0.55 if self.pack_size > 1 and not self.pack_is_alpha else 0.82 if self.pack_size > 1 else 1.0)
            if self.pack_resting:
                drift_speed *= 0.45
            new_tx = self.tx + self.dx * drift_speed * dt + sx + ax + px + fx + gx
            new_ty = self.ty + self.dy * drift_speed * dt + sy + ay + py + fy + gy
            old_tx, old_ty = self.tx, self.ty
            self.tx, self.ty, blocked = advance_until_blocked(
                grid, self.tx, self.ty, new_tx, new_ty
            )
            if blocked and abs(self.tx - old_tx) < 1e-6 and abs(self.ty - old_ty) < 1e-6:
                # Hit boundary — pick new direction
                angle   = random.uniform(0, 2 * math.pi)
                self.dx = math.cos(angle)
                self.dy = math.sin(angle)
                self._refresh_facing()
        elif (self.state in (Wolf.IDLE, Wolf.LOUNGE)
              and self.hunger <= WOLF_LOUNGE_HUNGER_TRIGGER
              and self._roll_timer <= 0
              and random.random() < WOLF_IDLE_ROLL_CHANCE * dt):
            self._roll_timer = random.uniform(WOLF_ROLL_MIN, WOLF_ROLL_MAX)

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
            corpse = self._hunt_target
            side = self._corpse_anchor_side(corpse) if corpse is not None else None
            if side == "left":
                key = "eat_right"
            elif side == "right":
                key = "eat_left"
            elif side == "top":
                key = "eat_front"
            else:
                key = "behind"
        elif self._roll_timer > 0 and self.facing in ("left", "right"):
            key = f"roll_{self.facing}"
        elif self.state in (Wolf.IDLE, Wolf.LOUNGE, Wolf.PLAY_SUBMIT):
            key = f"idle_{self.facing}"
        else:
            key = self.facing

        effective_ts = tile_size * self.size_scale
        sx_center_f  = self.tx * tile_size - cam_x
        sy_center_f  = self.ty * tile_size - cam_y
        sx_center    = round(sx_center_f)
        sy_center    = round(sy_center_f)

        if tile_size < Wolf.LOD_THRESHOLD:
            dot_r = max(1, round(effective_ts * 0.65))
            color = self._avg_colors.get(key, (170, 150, 120))
            pygame.draw.circle(screen, color, (sx_center, sy_center), dot_r)
            return

        sprite = self._scaled(key, effective_ts)
        w, h   = sprite.get_size()
        sx     = round(sx_center_f - w / 2)
        sy     = round(sy_center_f - h / 2)
        screen.blit(sprite, (sx, sy))

        # HP bar — show only when injured
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
