import pygame
import math
import random
import os

from mapgen import WATER, GRASS, DIRT

_SHEEP_DIR = os.path.join(os.path.dirname(__file__), "sheep experiment")

# ---------------------------------------------------------------------------
# Hunger / eating
# ---------------------------------------------------------------------------
HUNGER_RATE              = 0.006   # base hunger drain per second
EAT_RATE                 = 0.22
EAT_DURATION             = 3.5
HUNGER_THRESHOLD         = 0.55
HUNGER_URGENCY_THRESHOLD = 0.65
STARVING_THRESHOLD       = 0.80
HUNGER_DEATH             = 1.0
REGROWTH_TIME            = 90.0

# ---------------------------------------------------------------------------
# Lifespan  (extended)
# ---------------------------------------------------------------------------
LIFESPAN_MIN = 900.0    # 15 minutes
LIFESPAN_MAX = 1800.0   # 30 minutes

# ---------------------------------------------------------------------------
# Herding / flocking
# ---------------------------------------------------------------------------
HERD_RADIUS          = 14.0
HERD_COHESION_RADIUS = 22.0   # same-herd cohesion scan range
SEPARATION_RADIUS    = 1.6
SEPARATION_FORCE     = 1.4
COHESION_WEIGHT      = 0.55   # nearby-flock cohesion weight
SAME_HERD_WEIGHT     = 1.0    # multiplier on cohesion vector for same-herd members
OTHER_HERD_WEIGHT    = 0.18   # multiplier on cohesion vector for different-herd members
FOLLOW_RADIUS        = 9.0
FOLLOW_CHANCE        = 0.35

# Parent bond
PARENT_PULL_WEIGHT   = 0.65   # pull toward parent for young sheep
PARENT_AGE_CUTOFF    = 180.0  # sim-secs — parent bond fades linearly to zero

# ---------------------------------------------------------------------------
# Awareness
# ---------------------------------------------------------------------------
AWARENESS_RADIUS   = 28.0
MATE_SEARCH_RADIUS = 20.0

# ---------------------------------------------------------------------------
# Maturation
# ---------------------------------------------------------------------------
MATURITY_AGE = 90.0

# ---------------------------------------------------------------------------
# Reproduction  (significantly slowed down)
# ---------------------------------------------------------------------------
REPRODUCE_RADIUS   = 10.0
REPRODUCE_HUNGER   = 0.30
REPRODUCE_COOLDOWN = 480.0   # was 120 — 8 min between matings
BASE_LITTER        = 1       # was 2

# Gestation
GESTATION_DURATION    = 90.0   # seconds from mating to birth
GESTATION_HUNGER_MULT = 1.6    # pregnant sheep get hungry faster

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
        self.age      = float(age) if age is not None else random.uniform(MATURITY_AGE, MATURITY_AGE * 2)
        self.lifespan = random.uniform(LIFESPAN_MIN, LIFESPAN_MAX)

        # Genetic traits
        if genetic_size is not None:
            self.genetic_size = max(1.0 - GENETIC_SIZE_RANGE,
                                    min(1.0 + GENETIC_SIZE_RANGE, genetic_size))
        else:
            self.genetic_size = random.uniform(1.0 - GENETIC_SIZE_RANGE,
                                               1.0 + GENETIC_SIZE_RANGE)
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
        self.herd_pull_strength = 0.3      # cohesion weight toward center
        self.migration_mode    = False     # herd is migrating as one
        self.migrate_dx        = 0.0      # migration direction x
        self.migrate_dy        = 0.0      # migration direction y

        # Gestation
        self.pregnant        = False
        self.gestation_timer = 0.0
        self._pending_litter: list[tuple] = []   # (genetic_size, speed) per lamb

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
        growth = 0.5 + 0.5 * min(1.0, self.age / MATURITY_AGE)
        return growth * self.genetic_size

    @property
    def _reproduce_threshold(self) -> float:
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

        # --- Migration mode: follow herd direction with slight personal noise ---
        if self.migration_mode:
            noise = random.gauss(0, 0.18)
            angle_m = math.atan2(self.migrate_dy, self.migrate_dx) + noise
            self.dx = math.cos(angle_m)
            self.dy = math.sin(angle_m)
            self._refresh_facing()
            return

        # --- Base random direction ---
        angle = random.uniform(0, 2 * math.pi)
        bx    = math.cos(angle)
        by    = math.sin(angle)

        # Curious sheep weight random more; homebodies weight cohesion more
        rand_w = 0.25 + self.curiosity * 0.45

        # 1. Nearby flock cohesion
        if flock:
            hx, hy, count = 0.0, 0.0, 0
            for other in flock:
                if other is self:
                    continue
                ddx  = other.tx - self.tx
                ddy  = other.ty - self.ty
                dist = math.sqrt(ddx * ddx + ddy * ddy)
                same_herd = (self.herd_id != -1 and other.herd_id == self.herd_id)
                if same_herd and 0 < dist < HERD_COHESION_RADIUS:
                    hx += (ddx / dist) * SAME_HERD_WEIGHT
                    hy += (ddy / dist) * SAME_HERD_WEIGHT
                    count += 1
                elif not same_herd and 0 < dist < HERD_RADIUS:
                    hx += (ddx / dist) * OTHER_HERD_WEIGHT
                    hy += (ddy / dist) * OTHER_HERD_WEIGHT
                    count += 1
            if count > 0:
                hx /= count
                hy /= count
                cohesion_w = max(0.10, COHESION_WEIGHT - self.curiosity * 0.25)
                bx = bx * rand_w + hx * cohesion_w
                by = by * rand_w + hy * cohesion_w

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
            if other is self or not other.is_adult or other.infertile or other.pregnant:
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
            if other is self or not other.is_adult or other.infertile or other.pregnant:
                continue
            if other.hunger >= other._reproduce_threshold or other.reproduce_cooldown > 0:
                continue
            ddx  = other.tx - self.tx
            ddy  = other.ty - self.ty
            dist = math.sqrt(ddx * ddx + ddy * ddy)
            if dist > REPRODUCE_RADIUS:
                continue

            # 1 lamb normally; high-fertility sheep have a 25% chance of twins
            litter_count = BASE_LITTER
            if self.fertility > 0.8 and random.random() < 0.25:
                litter_count += 1

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
                pending.append((baby_size, baby_speed))

            self.pregnant        = True
            self.gestation_timer = GESTATION_DURATION
            self._pending_litter = pending

            self.reproduce_cooldown  = REPRODUCE_COOLDOWN
            other.reproduce_cooldown = REPRODUCE_COOLDOWN
            return

    def _birth(self, grid: list, new_sheep: list):
        """Spawn pending offspring when gestation completes."""
        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        for baby_size, baby_speed in self._pending_litter:
            attempts = 0
            while attempts < 8:
                attempts += 1
                ox = self.tx + random.uniform(-2.0, 2.0)
                oy = self.ty + random.uniform(-2.0, 2.0)
                c, r = int(ox), int(oy)
                if 0 <= r < rows and 0 <= c < cols and grid[r][c] != WATER:
                    baby          = Sheep(ox, oy, age=0.0, genetic_size=baby_size)
                    baby.hunger   = 0.0
                    baby.speed    = baby_speed
                    baby.herd_id  = self.herd_id   # born into mother's herd
                    baby.parent   = self            # bond to mother
                    new_sheep.append(baby)
                    break

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(self, dt: float, grid: list, regrowth_timers: dict,
               flock: list, new_sheep: list):
        if not self.alive:
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

        # Hunger — pregnant sheep get hungry faster
        hunger_mult = GESTATION_HUNGER_MULT if self.pregnant else 1.0
        self.hunger = min(1.0, self.hunger + HUNGER_RATE * self.genetic_size * hunger_mult * dt)

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
        key    = f"eat_{self.facing}" if self.state == Sheep.EAT else self.facing
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
