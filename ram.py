"""
Ram — male Sheep subclass with combat, exile, and herd dominance mechanics.

Key differences from Sheep (ewe):
  • 20% larger size_scale
  • 5% slower hunger accumulation
  • genetic_strength trait (heritable, drives combat outcomes)
  • State machine: normal → challenging → fighting → recovering → exiled
  • RamFight manages 1v1 fights; nearby bystanders are pushed away
"""

import math
import random
import os
import pygame

from mapgen import is_walkable_tile, advance_until_blocked
from sheep import (Sheep, HUNGER_RATE, HP_DRAIN_RATE, HP_MIN, DAY_DURATION,
                   GENETIC_STRENGTH_RANGE, REPRODUCE_COOLDOWN, MATE_SEARCH_RADIUS)
import sheep as _sheep_module

_RAM_DIR = os.path.join(os.path.dirname(__file__), "White Ram")

# ---------------------------------------------------------------------------
# Combat tuning — tweak these to adjust the feel of fights
# ---------------------------------------------------------------------------
FIGHT_EXCLUSION_RADIUS = 5.0    # tiles bystanders stay away from a fight
SQUARE_OFF_DIST        = 1.5    # each ram stands this far from fight center
CHARGE_SPEED           = 9.0    # tiles/sec during charge phase
RECOIL_SPEED           = 5.0    # tiles/sec bounce-back
SQUARE_OFF_DURATION    = 1.8    # seconds rams stare each other down before charging
CHARGE_DURATION        = 0.70   # seconds from charge start to impact
RECOIL_DURATION        = 0.50   # seconds of bounce-back animation

FIGHT_COOLDOWN_WIN     = 240.0  # seconds before winner can fight again (~48s at 5× speed)
FIGHT_COOLDOWN_LOSE    = 300.0  # seconds before loser can fight again
FERTILITY_BOOST_DUR    = 300.0  # seconds of boosted fertility after winning
STRENGTH_GAIN_CHANCE   = 0.20   # probability winner's strength increases
STRENGTH_GAIN_AMT      = 0.02   # how much it increases

MALE_HERD_CAP          = 0.10   # above this fraction of mature males, fighting pressure starts
RECOVERY_HP_TARGET     = 0.80   # fraction of max HP before leaving recovery state
CHALLENGE_SCAN_R       = 15.0   # tiles scanned for herd rivals
EXILE_HERD_SCAN_R      = 30.0   # tiles scanned for herds to challenge (exiled ram)
EXILE_WANDER_INTERVAL  = 60.0   # seconds between exile wander target picks
EXILE_FLEE_DURATION    = 90.0   # seconds of active fleeing after exile

# Fight outcome probabilities (must sum to 1.0)
PROB_FIGHT_TO_DEATH    = 0.10   # loser dies
PROB_FIGHT_EXILE       = 0.80   # loser cast out at ~30% HP
# remaining 10% = minor skirmish, loser stays but beaten at ~10% HP

END_HP_EXILE           = 0.30   # fraction of max HP that ends the exile-outcome fight
END_HP_MINOR           = 0.10   # fraction of max HP that ends the minor-outcome fight


# ===========================================================================
# RamFight
# ===========================================================================

class RamFight:
    """Manages a single active 1v1 fight between two rams."""

    OUTCOME_DEATH = "death"
    OUTCOME_EXILE = "exile"
    OUTCOME_MINOR = "minor"

    def __init__(self, challenger: 'Ram', defender: 'Ram'):
        # Determine fight outcome type at the start
        r = random.random()
        if r < PROB_FIGHT_TO_DEATH:
            self.outcome_type = RamFight.OUTCOME_DEATH
            self.end_hp_frac  = 0.0
        elif r < PROB_FIGHT_TO_DEATH + PROB_FIGHT_EXILE:
            self.outcome_type = RamFight.OUTCOME_EXILE
            self.end_hp_frac  = END_HP_EXILE
        else:
            self.outcome_type = RamFight.OUTCOME_MINOR
            self.end_hp_frac  = END_HP_MINOR

        # challenger fights from the left, defender from the right
        self.left  = challenger
        self.right = defender

        self.center_x = (challenger.tx + challenger.tx + defender.tx) / 2
        self.center_y = (challenger.ty + challenger.ty + defender.ty) / 2
        # Simpler: midpoint
        self.center_x = (challenger.tx + defender.tx) / 2
        self.center_y = (challenger.ty + defender.ty) / 2

        self.phase       = "squaring_off"
        self.phase_timer = SQUARE_OFF_DURATION
        self.done        = False
        self.winner: 'Ram | None' = None
        self.loser:  'Ram | None' = None

        challenger.ram_state = "fighting"
        defender.ram_state   = "fighting"
        challenger._fight    = self
        defender._fight      = self
        challenger._ramming  = False
        defender._ramming    = False

        self._reposition()

    # ------------------------------------------------------------------

    def _reposition(self):
        """Place both rams SQUARE_OFF_DIST from center, facing each other."""
        self.left.tx  = self.center_x - SQUARE_OFF_DIST
        self.left.ty  = self.center_y
        self.right.tx = self.center_x + SQUARE_OFF_DIST
        self.right.ty = self.center_y
        self.left.facing   = "right"
        self.right.facing  = "left"
        self.left.dx  =  1.0;  self.left.dy  = 0.0
        self.right.dx = -1.0;  self.right.dy = 0.0
        self.left.state  = Sheep.IDLE
        self.right.state = Sheep.IDLE

    def _fight_score(self, ram: 'Ram') -> float:
        """
        Normalised 0–1 fighting effectiveness score.
        Weighting: strength 50%, size 30%, speed 20%.
        Adds Gaussian noise so identical rams don't always tie.
        """
        str_norm  = (ram.genetic_strength - (1.0 - GENETIC_STRENGTH_RANGE)) / (2.0 * GENETIC_STRENGTH_RANGE)
        # size_scale for adult ram in normal range ≈ 0.8–1.8; normalise to 0–1
        size_norm = min(1.0, max(0.0, (ram.size_scale - 0.6) / 1.2))
        spd_norm  = (ram.speed - Sheep.SPEED_MIN) / max(0.1, Sheep.SPEED_MAX - Sheep.SPEED_MIN)
        raw = str_norm * 0.50 + size_norm * 0.30 + spd_norm * 0.20
        return max(0.02, raw + random.gauss(0, 0.08))

    def _do_impact(self):
        """Both rams take damage on each collision; stronger ram deals more."""
        score_l = self._fight_score(self.left)
        score_r = self._fight_score(self.right)
        # Damage dealt TO each ram = opponent's score × random multiplier
        dmg_to_left  = score_r * random.uniform(2.0, 3.5)
        dmg_to_right = score_l * random.uniform(2.0, 3.5)
        self.left.hp  = max(0.0, self.left.hp  - dmg_to_left)
        self.right.hp = max(0.0, self.right.hp - dmg_to_right)
        # Both get hungrier from exertion
        self.left.hunger  = min(1.0, self.left.hunger  + 0.04)
        self.right.hunger = min(1.0, self.right.hunger + 0.04)

    def _check_end(self) -> bool:
        max_l = float(self.left.genetic_hp)
        max_r = float(self.right.genetic_hp)
        if self.outcome_type == RamFight.OUTCOME_DEATH:
            return self.left.hp <= 0.0 or self.right.hp <= 0.0
        frac_l = self.left.hp  / max_l if max_l > 0 else 0.0
        frac_r = self.right.hp / max_r if max_r > 0 else 0.0
        return frac_l <= self.end_hp_frac or frac_r <= self.end_hp_frac

    def _resolve(self):
        """Determine winner, apply post-fight effects, set done=True."""
        max_l = float(self.left.genetic_hp)  if self.left.genetic_hp  > 0 else 1.0
        max_r = float(self.right.genetic_hp) if self.right.genetic_hp > 0 else 1.0
        frac_l = self.left.hp  / max_l
        frac_r = self.right.hp / max_r
        if frac_l >= frac_r:
            self.winner, self.loser = self.left, self.right
        else:
            self.winner, self.loser = self.right, self.left

        winner_old_herd = self.winner.herd_id
        loser_old_herd  = self.loser.herd_id

        # --- Winner effects ---
        self.winner.fight_cooldown          = FIGHT_COOLDOWN_WIN
        self.winner._fertility_boost_timer  = FERTILITY_BOOST_DUR
        self.winner.reproduce_cooldown      = 0.0   # can mate immediately after winning
        self.winner.hunger = min(1.0, self.winner.hunger + 0.15)
        self.winner.ram_state = "recovering"
        self.winner._fight   = None
        self.winner._ramming = False
        if random.random() < STRENGTH_GAIN_CHANCE:
            self.winner.genetic_strength = min(
                1.0 + GENETIC_STRENGTH_RANGE,
                self.winner.genetic_strength + STRENGTH_GAIN_AMT)
        # If the winner was exiled and the loser was in a herd, winner joins that herd
        if winner_old_herd < 0 and loser_old_herd >= 0:
            self.winner.herd_id   = loser_old_herd
            self.winner.ram_state = "recovering"

        # --- Loser effects ---
        self.loser.fight_cooldown = FIGHT_COOLDOWN_LOSE
        self.loser.hunger = min(1.0, self.loser.hunger + 0.20)
        self.loser._fight   = None
        self.loser._ramming = False

        if self.outcome_type == RamFight.OUTCOME_DEATH:
            self.loser._die()
        elif self.outcome_type == RamFight.OUTCOME_EXILE:
            self.loser.ram_state = "exiled"
            self.loser.herd_id   = -1
            self.loser._exile_flee_timer = EXILE_FLEE_DURATION
            self.loser._exile_flee_cx    = self.center_x
            self.loser._exile_flee_cy    = self.center_y
        else:  # MINOR — stays in herd but beaten
            self.loser.ram_state = "recovering"

        # Separate them so they don't stack
        offset = SQUARE_OFF_DIST
        self.winner.tx = self.center_x + (-offset if self.winner is self.left else offset)
        self.winner.ty = self.center_y

        self.done = True

    def update(self, dt: float):
        if self.done:
            return

        self.phase_timer -= dt

        if self.phase == "squaring_off":
            # Rams face each other — idle frame, no movement
            self.left.facing  = "right"
            self.right.facing = "left"
            self.left._ramming  = False
            self.right._ramming = False
            if self.phase_timer <= 0:
                self.phase       = "charging"
                self.phase_timer = CHARGE_DURATION

        elif self.phase == "charging":
            # Both rams charge toward the center using the ramming_speed sprite
            self.left._ramming  = True
            self.right._ramming = True
            cx = self.center_x
            if self.left.tx < cx - 0.12:
                self.left.tx = min(cx - 0.05, self.left.tx + CHARGE_SPEED * dt)
            if self.right.tx > cx + 0.12:
                self.right.tx = max(cx + 0.05, self.right.tx - CHARGE_SPEED * dt)

            if self.phase_timer <= 0:
                self._do_impact()
                if self._check_end():
                    self._resolve()
                    return
                self.phase       = "recoil"
                self.phase_timer = RECOIL_DURATION

        elif self.phase == "recoil":
            # Rams bounce apart
            self.left._ramming  = False
            self.right._ramming = False
            self.left.facing  = "right"
            self.right.facing = "left"
            self.left.tx  -= RECOIL_SPEED * dt
            self.right.tx += RECOIL_SPEED * dt
            if self.phase_timer <= 0:
                self.center_x = (self.left.tx + self.right.tx) / 2
                self.center_y = (self.left.ty + self.right.ty) / 2
                self._reposition()
                self.phase       = "squaring_off"
                self.phase_timer = SQUARE_OFF_DURATION


# ===========================================================================
# Ram
# ===========================================================================

class Ram(Sheep):
    """Male sheep with combat, exile, and herd dominance mechanics."""

    # Class-level sprite cache — completely separate from Sheep's cache
    _sprites_raw: dict | None = None
    _cache: dict  = {}
    _avg_colors: dict = {}

    # All active fights — updated each frame from main.py
    _active_fights: list = []

    # ------------------------------------------------------------------
    # Sprite loading
    # ------------------------------------------------------------------

    @classmethod
    def load_sprites(cls):
        if cls._sprites_raw is not None:
            return

        def _load(name):
            return pygame.image.load(os.path.join(_RAM_DIR, name)).convert_alpha()

        right     = _load("right_side_facing_ram.png")
        left      = pygame.transform.flip(right, True, False)
        eat_right = _load("right_side_facing _ram_eating.png")
        eat_left  = pygame.transform.flip(eat_right, True, False)
        dead_r    = _load("Dead_Ram_right.png")
        dead_l    = pygame.transform.flip(dead_r, True, False)
        decay_r   = _load("Ram_decaying_corpse.png")
        decay_l   = pygame.transform.flip(decay_r, True, False)
        ramming   = _load("Ramming_speed.png")
        ramming_l = pygame.transform.flip(ramming, True, False)
        front     = _load("front_facing_ram.png")
        behind    = _load("back_facing_ram.png")
        eat_front = _load("front_facing_ram_eating.png")

        cls._sprites_raw = {
            "front":         front,
            "behind":        behind,
            "right":         right,
            "left":          left,
            "eat_front":     eat_front,
            "eat_behind":    eat_front,   # no separate back-eating sprite; reuse front
            "eat_right":     eat_right,
            "eat_left":      eat_left,
            "dead_right":    dead_r,
            "dead_left":     dead_l,
            "decayed_right": decay_r,
            "decayed_left":  decay_l,
            "ramming_right": ramming,
            "ramming_left":  ramming_l,
        }
        cls._cache = {}
        cls._avg_colors = {k: cls._sample_avg_color(v)
                           for k, v in cls._sprites_raw.items()}

    # ------------------------------------------------------------------
    # Constructor
    # ------------------------------------------------------------------

    def __init__(self, tile_x: float, tile_y: float, age: float = None,
                 genetic_size: float = None, genetic_maturity: float = None,
                 genetic_lifespan: float = None, genetic_gestation: float = None,
                 genetic_hp: int = None, genetic_social: int = None,
                 genetic_strength: float = None):
        super().__init__(tile_x, tile_y, age=age,
                         genetic_size=genetic_size,
                         genetic_maturity=genetic_maturity,
                         genetic_lifespan=genetic_lifespan,
                         genetic_gestation=genetic_gestation,
                         genetic_hp=genetic_hp,
                         genetic_social=genetic_social,
                         genetic_strength=genetic_strength)
        self.sex = "male"

        # Ram-specific state flags
        self.ram_state: str              = "normal"
        self.fight_cooldown: float       = random.uniform(0.0, FIGHT_COOLDOWN_WIN * 0.5)
        self._fertility_boost_timer: float = 0.0
        self._fight: 'RamFight | None'   = None
        self._ramming: bool              = False

        # Challenge targeting
        self._challenge_target: 'Ram | None' = None
        self._challenge_timer: float     = 0.0   # give-up countdown

        # Exile state
        self._exile_wander_timer: float  = EXILE_WANDER_INTERVAL
        self._exile_flee_timer: float    = 0.0
        self._exile_flee_cx: float       = tile_x
        self._exile_flee_cy: float       = tile_y

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def size_scale(self) -> float:
        """Rams are on average 20% larger than ewes at equivalent genetics/age."""
        return super().size_scale * 1.20

    @property
    def _hunger_rate_mult(self) -> float:
        return 0.95   # 5% slower hunger accumulation than ewes

    @property
    def is_mature_ram(self) -> bool:
        return self.is_adult

    # ------------------------------------------------------------------
    # Class-level fight management  (called from main.py each frame)
    # ------------------------------------------------------------------

    @classmethod
    def update_fights(cls, dt: float):
        for fight in cls._active_fights:
            fight.update(dt)
        cls._active_fights = [f for f in cls._active_fights if not f.done]

    @classmethod
    def _start_fight(cls, challenger: 'Ram', defender: 'Ram'):
        # Guard: neither should already be fighting
        if (getattr(challenger, 'ram_state', '') == 'fighting' or
                getattr(defender, 'ram_state', '') == 'fighting'):
            return
        cls._active_fights.append(RamFight(challenger, defender))

    # ------------------------------------------------------------------
    # Challenge helpers
    # ------------------------------------------------------------------

    def _mature_males_in_herd(self, flock: list) -> tuple:
        """(n_mature_males, n_adults) in self's herd."""
        if self.herd_id < 0:
            return 0, 0
        adults = [a for a in flock
                  if a.herd_id == self.herd_id
                  and a.dead_state is None and a.is_adult]
        males  = [a for a in adults if getattr(a, 'sex', 'female') == 'male']
        return len(males), len(adults)

    def _find_rival_in_herd(self, flock: list) -> 'Ram | None':
        """Another challengeable mature male in the same herd."""
        for a in flock:
            if (a is not self
                    and a.herd_id == self.herd_id
                    and a.dead_state is None
                    and a.is_adult
                    and getattr(a, 'sex', 'female') == 'male'
                    and getattr(a, 'ram_state', 'normal') == 'normal'
                    and getattr(a, 'fight_cooldown', 1.0) <= 0):
                return a
        return None

    def _find_rival_for_exile_challenge(self, flock: list) -> 'Ram | None':
        """Resident ram in the nearest foreign herd (for exiled challengers)."""
        best_sq = EXILE_HERD_SCAN_R ** 2
        best    = None
        for a in flock:
            if (a is not self
                    and a.herd_id >= 0
                    and a.dead_state is None
                    and a.is_adult
                    and getattr(a, 'sex', 'female') == 'male'
                    and getattr(a, 'ram_state', 'normal') == 'normal'
                    and getattr(a, 'fight_cooldown', 1.0) <= 0):
                d2 = (a.tx - self.tx) ** 2 + (a.ty - self.ty) ** 2
                if d2 < best_sq:
                    best_sq = d2
                    best    = a
        return best

    def _check_challenge(self, flock: list):
        """Trigger a challenge if the herd has too many mature males."""
        n_males, n_adults = self._mature_males_in_herd(flock)
        if n_adults > 0 and n_males >= 2:
            if n_males / n_adults > MALE_HERD_CAP:
                rival = self._find_rival_in_herd(flock)
                if rival is not None:
                    self.ram_state         = "challenging"
                    self._challenge_target = rival
                    self._challenge_timer  = 30.0

    def _exile_behavior(self, dt: float, flock: list):
        """Exiled ram: flee old fight center, then wander; occasionally seek herds."""
        # Urgent flee phase
        if self._exile_flee_timer > 0:
            self._exile_flee_timer -= dt
            fx = self.tx - self._exile_flee_cx
            fy = self.ty - self._exile_flee_cy
            fd = math.sqrt(fx * fx + fy * fy)
            if fd > 0:
                self.dx = fx / fd
                self.dy = fy / fd
                self._refresh_facing()
                self.state = Sheep.WALK
        # Periodic wander / herd-challenge scan
        self._exile_wander_timer -= dt
        if self._exile_wander_timer <= 0:
            self._exile_wander_timer = EXILE_WANDER_INTERVAL * random.uniform(0.7, 1.3)
            if self.is_mature_ram and self.fight_cooldown <= 0:
                rival = self._find_rival_for_exile_challenge(flock)
                if rival is not None:
                    self.ram_state         = "challenging"
                    self._challenge_target = rival

    # ------------------------------------------------------------------
    # Movement while challenging (walk toward target)
    # ------------------------------------------------------------------

    def _challenge_walk(self, dt: float, grid: list, flock: list) -> bool:
        """
        Walk toward challenge target.  Returns True if still active,
        False if the challenge should be abandoned.
        """
        target = self._challenge_target
        self._challenge_timer -= dt

        if (target is None
                or not target.alive
                or target.dead_state is not None
                or self._challenge_timer <= 0
                or getattr(target, 'ram_state', 'normal') == 'fighting'):
            return False  # give up

        ddx  = target.tx - self.tx
        ddy  = target.ty - self.ty
        dist = math.sqrt(ddx * ddx + ddy * ddy)

        if dist < SQUARE_OFF_DIST * 2.5 and getattr(target, 'fight_cooldown', 1.0) <= 0:
            Ram._start_fight(self, target)
            return True   # fight started; update() will handle state

        if dist > 0:
            self.dx = ddx / dist
            self.dy = ddy / dist
        self._refresh_facing()
        self.state = Sheep.WALK

        rows = len(grid)
        cols = len(grid[0]) if grid else 0
        sx, sy = self._separation_delta(flock, dt)
        new_tx = self.tx + self.dx * self.speed * 1.3 * dt + sx
        new_ty = self.ty + self.dy * self.speed * 1.3 * dt + sy
        self.tx, self.ty, _ = advance_until_blocked(grid, self.tx, self.ty, new_tx, new_ty)
        return True

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(self, dt: float, grid: list, regrowth_timers: dict,
               flock: list, new_sheep: list, dirty_callback=None):

        # Tick persistent timers regardless of state
        if self.fight_cooldown > 0:
            self.fight_cooldown = max(0.0, self.fight_cooldown - dt)
        if self._fertility_boost_timer > 0:
            self._fertility_boost_timer = max(0.0, self._fertility_boost_timer - dt)

        # ── FIGHTING ────────────────────────────────────────────────────────
        # RamFight controls positioning; we just keep the ram alive
        if self.ram_state == "fighting":
            if not self.alive:
                return
            if self.dead_state is not None:
                self._update_corpse(dt, grid, regrowth_timers, dirty_callback)
                return
            self.age += dt
            if self.reproduce_cooldown > 0:
                self.reproduce_cooldown = max(0.0, self.reproduce_cooldown - dt)
            hp_hunger_mult = 1.0 + (self.genetic_hp - HP_MIN) * 0.01
            self.hunger = min(1.0,
                self.hunger + HUNGER_RATE * self.genetic_size * hp_hunger_mult * 0.95 * dt)
            alpha = dt / DAY_DURATION
            self._avg_hunger += (self.hunger - self._avg_hunger) * min(1.0, alpha)
            if self.hunger >= 1.0:
                self.hp = max(0.0, self.hp - HP_DRAIN_RATE * dt)
            if self.age >= self._effective_lifespan or self.hp <= 0:
                self._die()
                # Emergency resolve — tell fight manager this ram is gone
                if self._fight is not None and not self._fight.done:
                    other = (self._fight.right
                             if self._fight.left is self
                             else self._fight.left)
                    self._fight.done = True
                    if other.alive and other.dead_state is None:
                        other.ram_state = "recovering"
                        other._fight    = None
                        other._ramming  = False
            return

        # ── CHALLENGING (or exiled challenger) ──────────────────────────────
        if self.ram_state in ("challenging",):
            exiling = (self.herd_id < 0)
            still_active = self._challenge_walk(dt, grid, flock)
            if not still_active:
                self.ram_state         = "exiled" if exiling else "normal"
                self._challenge_target = None
            # Minimal survival ticks
            self.age += dt
            if self.reproduce_cooldown > 0:
                self.reproduce_cooldown = max(0.0, self.reproduce_cooldown - dt)
            hp_hunger_mult = 1.0 + (self.genetic_hp - HP_MIN) * 0.01
            self.hunger = min(1.0,
                self.hunger + HUNGER_RATE * self.genetic_size * hp_hunger_mult * 0.95 * dt)
            alpha = dt / DAY_DURATION
            self._avg_hunger += (self.hunger - self._avg_hunger) * min(1.0, alpha)
            if self.hunger >= 1.0:
                self.hp = max(0.0, self.hp - HP_DRAIN_RATE * dt)
            if self.age >= self._effective_lifespan or self.hp <= 0:
                self._die()
            return

        # ── EXILED ──────────────────────────────────────────────────────────
        if self.ram_state == "exiled":
            self._exile_behavior(dt, flock)
            # During the urgent flee phase, handle movement manually so the ram
            # doesn't eat a strip of grass as he leaves the fight.
            if self._exile_flee_timer > 0:
                self.age += dt
                if self.reproduce_cooldown > 0:
                    self.reproduce_cooldown = max(0.0, self.reproduce_cooldown - dt)
                hp_hunger_mult = 1.0 + (self.genetic_hp - HP_MIN) * 0.01
                self.hunger = min(1.0,
                    self.hunger + HUNGER_RATE * self.genetic_size * hp_hunger_mult * 0.95 * dt)
                alpha = dt / DAY_DURATION
                self._avg_hunger += (self.hunger - self._avg_hunger) * min(1.0, alpha)
                if self.hunger >= 1.0:
                    self.hp = max(0.0, self.hp - HP_DRAIN_RATE * dt)
                if self.age >= self._effective_lifespan or self.hp <= 0:
                    self._die()
                    return
                # Move away without eating
                if self.state == Sheep.WALK:
                    rows = len(grid)
                    cols = len(grid[0]) if grid else 0
                    sx, sy = self._separation_delta(flock, dt)
                    new_tx = self.tx + self.dx * self.speed * dt + sx
                    new_ty = self.ty + self.dy * self.speed * dt + sy
                    self.tx, self.ty, _ = advance_until_blocked(
                        grid, self.tx, self.ty, new_tx, new_ty
                    )
            else:
                # Normal exile behavior — can graze, wander, seek herds
                super().update(dt, grid, regrowth_timers, flock, new_sheep, dirty_callback)
            return

        # ── NORMAL / RECOVERING ─────────────────────────────────────────────
        super().update(dt, grid, regrowth_timers, flock, new_sheep, dirty_callback)

        if not self.alive or self.dead_state is not None:
            return

        if self.ram_state == "recovering":
            # Leave recovery once HP is mostly restored
            if self.hp >= float(self.genetic_hp) * RECOVERY_HP_TARGET:
                self.ram_state = "normal"

        elif self.ram_state == "normal":
            if self.is_mature_ram and self.fight_cooldown <= 0:
                self._check_challenge(flock)

    # ------------------------------------------------------------------
    # Reproduction override: rams don't carry pregnancies
    # ------------------------------------------------------------------

    def _try_reproduce(self, flock: list, grid: list, new_sheep: list):
        """Rams don't get pregnant. Ewes find rams via their own _try_reproduce."""
        return

    def _find_nearest_mate(self, flock: list):
        """Override: alpha rams search the full herd awareness radius for eligible ewes."""
        if not self.is_adult or self.herd_id < 0:
            return None
        search_r = max(MATE_SEARCH_RADIUS,
                       getattr(self, 'herd_awareness_r', MATE_SEARCH_RADIUS))
        best_dist = float('inf')
        best_dx, best_dy = 0.0, 0.0
        found = False
        for other in flock:
            if (other is self
                    or other.dead_state is not None
                    or not other.is_adult
                    or other.infertile
                    or other.pregnant
                    or getattr(other, 'sex', 'female') == 'male'
                    or other.herd_id != self.herd_id):
                continue
            if other.hunger >= other._reproduce_threshold or other.reproduce_cooldown > 0:
                continue
            ddx  = other.tx - self.tx
            ddy  = other.ty - self.ty
            dist = math.sqrt(ddx * ddx + ddy * ddy)
            if dist < best_dist and dist <= search_r:
                best_dist = dist
                if dist > 0:
                    best_dx = ddx / dist
                    best_dy = ddy / dist
                found = True
        return (best_dx, best_dy) if found else None

    # ------------------------------------------------------------------
    # Draw — uses Ram sprites; handles ramming_speed frame
    # ------------------------------------------------------------------

    def draw(self, screen: pygame.Surface, cam_x: float, cam_y: float, tile_size: float):
        if self.dead_state == "fresh":
            key = "dead_left" if self.death_facing == "left" else "dead_right"
        elif self.dead_state == "decayed":
            key = "decayed_left" if self.death_facing == "left" else "decayed_right"
        elif self._ramming:
            # Ramming_speed is a low side-on charging pose.
            # Mirror it based on facing so the ram always charges toward his opponent.
            key = "ramming_left" if self.facing == "left" else "ramming_right"
        elif self.state == Sheep.EAT:
            key = f"eat_{self.facing}"
        else:
            key = self.facing

        effective_ts = tile_size * self.size_scale
        sx_center_f  = self.tx * tile_size - cam_x
        sy_center_f  = self.ty * tile_size - cam_y
        sx_center    = round(sx_center_f)
        sy_center    = round(sy_center_f)

        if tile_size < Ram.LOD_THRESHOLD:
            dot_r = max(1, round(effective_ts * 0.6))
            color = self._avg_colors.get(key, (215, 200, 185))
            pygame.draw.circle(screen, color, (sx_center, sy_center), dot_r)
            return

        sprite = self._scaled(key, effective_ts)
        w, h   = sprite.get_size()
        sx     = round(sx_center_f - w / 2)
        sy     = round(sy_center_f - h / 2)
        screen.blit(sprite, (sx, sy))

        # HP bar — shown only when injured
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


# ---------------------------------------------------------------------------
# Register offspring factory — allows Sheep._birth to spawn Ram instances
# without a circular import.  Called once at module load.
# ---------------------------------------------------------------------------

def _make_offspring(ox: float, oy: float, sex: str, **kwargs) -> Sheep:
    if sex == "male":
        r = Ram(ox, oy, **kwargs)
        r.fight_cooldown = 0.0   # newborns start with no cooldown
        return r
    return Sheep(ox, oy, **kwargs)

_sheep_module._OFFSPRING_FACTORY = _make_offspring
