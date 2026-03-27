import pygame
import math
import random
import os

from mapgen import WATER, GRASS, DIRT

_SHEEP_DIR = os.path.join(os.path.dirname(__file__), "sheep experiment")

# Hunger / eating
HUNGER_RATE              = 0.006   # slow hunger drain
EAT_RATE                 = 0.22
EAT_DURATION             = 3.5
HUNGER_THRESHOLD         = 0.55
HUNGER_URGENCY_THRESHOLD = 0.65   # above this, sheep move faster
STARVING_THRESHOLD       = 0.80   # above this, ONLY seek food — no reproduction
HUNGER_DEATH             = 1.0    # die of starvation
REGROWTH_TIME            = 45.0

# Lifespan
LIFESPAN_MIN = 600.0    # 10 minutes
LIFESPAN_MAX = 1200.0   # 20 minutes

# Herding / flocking
HERD_RADIUS       = 14.0
SEPARATION_RADIUS = 1.6
SEPARATION_FORCE  = 1.4
COHESION_WEIGHT   = 0.60
FOLLOW_RADIUS     = 9.0
FOLLOW_CHANCE     = 0.35

# Awareness
AWARENESS_RADIUS   = 28.0   # how far sheep scan for grass (tiles)
MATE_SEARCH_RADIUS = 20.0   # how far to scan for a compatible mate

# Maturation
MATURITY_AGE = 90.0   # seconds until full adult size

# Reproduction
REPRODUCE_RADIUS   = 10.0   # tiles — max distance between mates
REPRODUCE_HUNGER   = 0.30   # base hunger threshold to reproduce (lower = more full)
REPRODUCE_COOLDOWN = 120.0  # seconds between litters per sheep
BASE_LITTER        = 2

# Genetics
GENETIC_SIZE_RANGE = 0.15   # adults range from 0.85× to 1.15× base size


class Sheep:
    IDLE = "idle"
    WALK = "walk"
    EAT  = "eat"

    SPEED_MIN = 3.5
    SPEED_MAX = 6.0

    _sprites_raw: dict | None = None
    _cache: dict = {}

    def __init__(self, tile_x: float, tile_y: float, age: float = None,
                 genetic_size: float = None):
        self.tx = float(tile_x)
        self.ty = float(tile_y)
        self.dx = 0.0
        self.dy = 0.0
        self.facing  = "front"
        self.state   = Sheep.IDLE
        self.timer   = 0.0
        self.hunger  = random.uniform(0.1, 0.45)
        self.speed   = random.uniform(Sheep.SPEED_MIN, Sheep.SPEED_MAX)
        # Age — newborns pass age=0; spawned sheep get a random adult age
        self.age      = float(age) if age is not None else random.uniform(MATURITY_AGE, MATURITY_AGE * 2)
        self.lifespan = random.uniform(LIFESPAN_MIN, LIFESPAN_MAX)

        # Genetic traits
        if genetic_size is not None:
            self.genetic_size = max(1.0 - GENETIC_SIZE_RANGE,
                                    min(1.0 + GENETIC_SIZE_RANGE, genetic_size))
        else:
            self.genetic_size = random.uniform(1.0 - GENETIC_SIZE_RANGE,
                                               1.0 + GENETIC_SIZE_RANGE)
        self.infertile = random.random() < 0.001   # 0.1% chance
        self.genius    = random.random() < 0.001   # 0.1% chance (behavior TBD)

        self.alive     = True
        self.fertility = random.uniform(0.3, 1.0)
        self.reproduce_cooldown = random.uniform(0, REPRODUCE_COOLDOWN * 0.5)
        self._schedule_idle()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_adult(self) -> bool:
        return self.age >= MATURITY_AGE

    @property
    def size_scale(self) -> float:
        """0.5 at birth → genetic_size (0.85–1.15) at full maturity."""
        growth = 0.5 + 0.5 * min(1.0, self.age / MATURITY_AGE)
        return growth * self.genetic_size

    @property
    def _reproduce_threshold(self) -> float:
        """Bigger sheep must be more full (lower hunger) before they can reproduce."""
        return REPRODUCE_HUNGER / self.genetic_size

    # ------------------------------------------------------------------
    # Sprite loading and per-tile-size caching
    # ------------------------------------------------------------------

    @classmethod
    def load_sprites(cls):
        if cls._sprites_raw is not None:
            return
        front     = pygame.image.load(os.path.join(_SHEEP_DIR, "Front_Facing.png")).convert_alpha()
        behind    = pygame.image.load(os.path.join(_SHEEP_DIR, "Behind_Facing.png")).convert_alpha()
        right     = pygame.image.load(os.path.join(_SHEEP_DIR, "Right_Facing.png")).convert_alpha()
        left      = pygame.transform.flip(right, True, False)
        eat_front = pygame.image.load(os.path.join(_SHEEP_DIR, "Eating_Grass_Forward_Facing.png")).convert_alpha()
        eat_right = pygame.image.load(os.path.join(_SHEEP_DIR, "Facing_To_The_Right_Eating_Grass.png")).convert_alpha()
        eat_left  = pygame.transform.flip(eat_right, True, False)
        cls._sprites_raw = {
            "front":      front,
            "behind":     behind,
            "right":      right,
            "left":       left,
            "eat_front":  eat_front,
            "eat_behind": eat_front,
            "eat_right":  eat_right,
            "eat_left":   eat_left,
        }
        cls._cache = {}

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

        angle = random.uniform(0, 2 * math.pi)
        rdx   = math.cos(angle)
        rdy   = math.sin(angle)

        if flock:
            hx, hy, count = 0.0, 0.0, 0
            for other in flock:
                if other is self:
                    continue
                ddx  = other.tx - self.tx
                ddy  = other.ty - self.ty
                dist = math.sqrt(ddx * ddx + ddy * ddy)
                if 0 < dist < HERD_RADIUS:
                    hx += ddx / dist
                    hy += ddy / dist
                    count += 1
            if count > 0:
                hx /= count
                hy /= count
                bx  = rdx * (1.0 - COHESION_WEIGHT) + hx * COHESION_WEIGHT
                by  = rdy * (1.0 - COHESION_WEIGHT) + hy * COHESION_WEIGHT
                mag = math.sqrt(bx * bx + by * by)
                if mag > 0:
                    rdx = bx / mag
                    rdy = by / mag

        self.dx = rdx
        self.dy = rdy
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
            if 0 < dist < SEPARATION_RADIUS:
                strength = (SEPARATION_RADIUS - dist) / SEPARATION_RADIUS
                sx += (ddx / dist) * strength * SEPARATION_FORCE * dt
                sy += (ddy / dist) * strength * SEPARATION_FORCE * dt
        return sx, sy

    def _try_follow(self, flock: list) -> bool:
        for other in flock:
            if other is self or other.state != Sheep.WALK:
                continue
            ddx  = other.tx - self.tx
            ddy  = other.ty - self.ty
            dist = math.sqrt(ddx * ddx + ddy * ddy)
            if dist < FOLLOW_RADIUS and random.random() < FOLLOW_CHANCE:
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
        """Return normalized (dx, dy) toward nearest grass within AWARENESS_RADIUS, or None."""
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
        """Return normalized (dx, dy) toward nearest compatible mate within MATE_SEARCH_RADIUS, or None."""
        best_dist = float('inf')
        best_dx, best_dy = 0.0, 0.0
        found = False

        for other in flock:
            if other is self or not other.is_adult or other.infertile:
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

    def _try_reproduce(self, flock: list, grid: list, new_sheep: list):
        """Find a nearby well-fed adult mate and produce offspring."""
        if self.infertile:
            return
        for other in flock:
            if other is self or not other.is_adult or other.infertile:
                continue
            if other.hunger >= other._reproduce_threshold or other.reproduce_cooldown > 0:
                continue
            ddx  = other.tx - self.tx
            ddy  = other.ty - self.ty
            dist = math.sqrt(ddx * ddx + ddy * ddy)
            if dist > REPRODUCE_RADIUS:
                continue

            litter = BASE_LITTER + int(self.fertility * 2)   # 2–4
            if self.hunger > 0.15:
                litter = max(1, litter - 1)

            rows = len(grid)
            cols = len(grid[0]) if rows else 0
            spawned = 0
            attempts = 0
            while spawned < litter and attempts < litter * 6:
                attempts += 1
                ox = self.tx + random.uniform(-2.0, 2.0)
                oy = self.ty + random.uniform(-2.0, 2.0)
                c, r = int(ox), int(oy)
                if 0 <= r < rows and 0 <= c < cols and grid[r][c] not in (WATER,):
                    # Inherit size from both parents with small mutation
                    parent_size = (self.genetic_size + other.genetic_size) / 2.0
                    baby_size = max(1.0 - GENETIC_SIZE_RANGE,
                                   min(1.0 + GENETIC_SIZE_RANGE,
                                       parent_size + random.gauss(0, 0.03)))
                    baby = Sheep(ox, oy, age=0.0, genetic_size=baby_size)
                    baby.hunger = 0.0
                    mid_speed = (self.speed + other.speed) / 2.0
                    baby.speed = max(Sheep.SPEED_MIN * 0.7,
                                     min(Sheep.SPEED_MAX * 1.3,
                                         mid_speed + random.gauss(0, 0.15)))
                    new_sheep.append(baby)
                    spawned += 1

            self.reproduce_cooldown  = REPRODUCE_COOLDOWN
            other.reproduce_cooldown = REPRODUCE_COOLDOWN
            return   # one mate per idle cycle

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(self, dt: float, grid: list, regrowth_timers: dict,
               flock: list, new_sheep: list):
        if not self.alive:
            return

        self.age    = self.age + dt
        # Bigger sheep metabolize faster and get hungry sooner
        self.hunger = min(1.0, self.hunger + HUNGER_RATE * self.genetic_size * dt)
        self.timer -= dt
        if self.reproduce_cooldown > 0:
            self.reproduce_cooldown = max(0.0, self.reproduce_cooldown - dt)

        # --- Death checks ---
        if self.age >= self.lifespan or self.hunger >= HUNGER_DEATH:
            self.alive = False
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
            self._schedule_eat()
            return

        # --- State transitions ---

        if starving:
            # Total override — nothing matters except finding food
            if self.state == Sheep.IDLE or self.timer <= 0:
                direction = self._find_nearest_grass(grid, rows, cols)
                if direction:
                    self.state = Sheep.WALK
                    self.dx, self.dy = direction
                    self.timer = 1.5   # recheck direction frequently
                    self._refresh_facing()
                else:
                    self._schedule_walk(flock)

        elif self.state == Sheep.IDLE:
            # Hungry but not starving: shorten idle wait
            if urgency > 0 and self.timer > 0.4:
                self.timer = min(self.timer, max(0.4, 1.0 - urgency * 0.7))

            if self.timer <= 0:
                ready_to_mate = (self.is_adult
                                 and not self.infertile
                                 and self.hunger < self._reproduce_threshold
                                 and self.reproduce_cooldown <= 0)
                if ready_to_mate:
                    self._try_reproduce(flock, grid, new_sheep)
                    # If no mate was within REPRODUCE_RADIUS, walk toward one
                    if self.reproduce_cooldown <= 0:
                        mate_dir = self._find_nearest_mate(flock)
                        if mate_dir:
                            self.state = Sheep.WALK
                            self.dx, self.dy = mate_dir
                            self.timer = 2.0
                            self._refresh_facing()
                            # skip normal movement scheduling
                            return  # movement applied below on next tick

                if not self._try_follow(flock):
                    # Seek grass if getting hungry
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

        # --- Movement (always applies when walking) ---
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
        key    = f"eat_{self.facing}" if self.state == Sheep.EAT else self.facing
        # Scale tile_size by growth so babies are drawn smaller; adults vary by genetic_size
        effective_ts = tile_size * self.size_scale
        sprite = self._scaled(key, effective_ts)
        ts     = max(1, round(tile_size))
        w, h   = sprite.get_size()
        sx     = int(self.tx * ts - cam_x) - w // 2
        sy     = int(self.ty * ts - cam_y) - h // 2
        screen.blit(sprite, (sx, sy))

        # Hunger bar (hidden when well-fed)
        if self.hunger > 0.2:
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
