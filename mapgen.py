import random
import math
from collections import deque

WATER = "water"
SAND  = "sand"
DIRT  = "dirt"
GRASS = "grass"

TERRAIN_ORDER = [WATER, SAND, DIRT, GRASS]


class MapGenerator:
    """
    Generates a 2D grid of terrain types representing a randomized island.

    Island shape uses a radial gradient blended with fractional Brownian motion
    (multi-octave value noise) so the coastline is irregular rather than circular.

    Terrain is assigned by threshold on the blended value:
        low  → water → sand → dirt → grass  → high

    Usage:
        gen  = MapGenerator(width=64, height=64, seed=42)
        grid = gen.generate()           # list[list[str]]
        tile = gen.get_tile(x, y)       # str terrain constant
        nbrs = gen.get_neighbors(x, y)  # dict[direction, str]
    """

    def __init__(self, width: int = 64, height: int = 64, seed: int = None):
        self.width  = width
        self.height = height
        self.seed   = seed if seed is not None else random.randint(0, 999_999)
        self.grid   = None  # populated by generate()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    # Resolution of the pre-computed FBM noise table.
    _NOISE_RES = 256
    # Resolution at which terrain values are computed before scaling to full size.
    # Terrain boundaries have ~(W/COMPUTE_RES)-tile granularity; at default zoom
    # each tile is small enough that 2-tile stepping is imperceptible.
    _COMPUTE_RES = 512

    def generate(self) -> list[list[str]]:
        cx = self.width  / 2.0
        cy = self.height / 2.0
        max_dist = min(cx, cy) * 0.60  # island uses 60% of radius, leaving water border

        self._arms = self._generate_arms(cx, cy, max_dist)

        # Pre-compute FBM and spatial contribution tables at low resolution,
        # compute terrain values at COMPUTE_RES, then scale up to full size.
        _NR  = self._NOISE_RES
        _CR  = self._COMPUTE_RES
        noise_tbl  = self._precompute_fbm(4.5, 4.5, _NR, 5)
        radial_tbl = self._island_precompute_radial(_NR, cx, cy, max_dist)
        arm_tbl    = self._island_precompute_arms(_NR, cx, cy)

        # Compute terrain at _CR × _CR
        terrain_lr = []
        for r in range(_CR):
            row = []
            py  = r / _CR * _NR
            for c in range(_CR):
                px     = c / _CR * _NR
                noise  = self._sample_noise(noise_tbl,  _NR, px, py)
                radial = self._sample_noise(radial_tbl, _NR, px, py)
                arm    = self._sample_noise(arm_tbl,    _NR, px, py)
                v      = min(1.0, radial * 0.62 + noise * 0.38 + arm)
                row.append(self._terrain_from_value(v))
            terrain_lr.append(row)

        # Scale up to full resolution (nearest-neighbour)
        self.grid = self._scale_terrain(terrain_lr, _CR, self.width, self.height)
        return self.grid

    def _island_precompute_radial(self, res, cx, cy, max_dist):
        W, H = self.width, self.height
        tbl  = [0.0] * (res * res)
        for r in range(res):
            y  = r / res * H
            dy = (y - cy) / max_dist
            bi = r * res
            for c in range(res):
                x  = c / res * W
                dx = (x - cx) / max_dist
                tbl[bi + c] = max(0.0, 1.0 - math.sqrt(dx*dx + dy*dy))
        return tbl

    def _island_precompute_arms(self, res, cx, cy):
        if not self._arms:
            return [0.0] * (res * res)
        W, H = self.width, self.height
        tbl  = [0.0] * (res * res)
        for r in range(res):
            y  = r / res * H
            bi = r * res
            for c in range(res):
                x = c / res * W
                tbl[bi + c] = self._arm_contribution(x, y, cx, cy)
        return tbl

    @staticmethod
    def _scale_terrain(terrain_lr: list, lr_size: int,
                       target_w: int, target_h: int) -> list:
        """Nearest-neighbour scale-up from lr_size×lr_size to target_w×target_h."""
        grid = []
        for r in range(target_h):
            sr   = min(lr_size - 1, int(r / target_h * lr_size))
            src_row = terrain_lr[sr]
            row  = [src_row[min(lr_size - 1, int(c / target_w * lr_size))]
                    for c in range(target_w)]
            grid.append(row)
        return grid

    def get_tile(self, x: int, y: int) -> str:
        """Returns terrain at (x, y). Out-of-bounds tiles are treated as water."""
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.grid[y][x]
        return WATER

    def get_neighbors(self, x: int, y: int) -> dict[str, str]:
        """
        Returns all 8 cardinal + diagonal neighbor terrain types.
        Keys: N, NE, E, SE, S, SW, W, NW
        """
        return {
            "N":  self.get_tile(x,     y - 1),
            "NE": self.get_tile(x + 1, y - 1),
            "E":  self.get_tile(x + 1, y),
            "SE": self.get_tile(x + 1, y + 1),
            "S":  self.get_tile(x,     y + 1),
            "SW": self.get_tile(x - 1, y + 1),
            "W":  self.get_tile(x - 1, y),
            "NW": self.get_tile(x - 1, y - 1),
        }

    # ------------------------------------------------------------------
    # Internal generation helpers
    # ------------------------------------------------------------------

    def _island_value(self, x: int, y: int,
                      cx: float, cy: float, max_dist: float) -> float:
        """Legacy per-pixel path (kept for subclass override compatibility)."""
        dx = (x - cx) / max_dist
        dy = (y - cy) / max_dist
        dist     = math.sqrt(dx * dx + dy * dy)
        radial   = max(0.0, 1.0 - dist)
        nx = x / self.width  * 4.5
        ny = y / self.height * 4.5
        noise = self._fbm(nx, ny, octaves=5)
        base = radial * 0.62 + noise * 0.38
        arm = self._arm_contribution(x, y, cx, cy)
        return min(1.0, base + arm)

    def _island_value_fast(self, x: int, y: int,
                           cx: float, cy: float, max_dist: float,
                           noise: float) -> float:
        """Fast path used by generate(): noise is already pre-sampled."""
        dx = (x - cx) / max_dist
        dy = (y - cy) / max_dist
        dist   = math.sqrt(dx * dx + dy * dy)
        radial = max(0.0, 1.0 - dist)
        base   = radial * 0.62 + noise * 0.38
        arm    = self._arm_contribution(x, y, cx, cy)
        return min(1.0, base + arm)

    def _generate_arms(self, cx: float, cy: float,
                       max_dist: float) -> list:
        """Return a list of (angle, length, width) peninsula arm descriptors."""
        rng = random.Random(self.seed ^ 0xBEEF_CAFE)
        # ~60% chance of any peninsulas on a given map
        if rng.random() < 0.40:
            return []
        n_arms = rng.randint(1, 3)
        arms = []
        for _ in range(n_arms):
            angle  = rng.uniform(0, 2 * math.pi)
            # length relative to island radius — extends well past the coast
            length = rng.uniform(0.65, 1.20) * max_dist
            width  = rng.uniform(0.09, 0.18) * max_dist
            arms.append((angle, length, width))
        return arms

    def _arm_contribution(self, x: int, y: int,
                          cx: float, cy: float) -> float:
        """Boost terrain value along peninsula arms."""
        if not self._arms:
            return 0.0
        best = 0.0
        for angle, length, width in self._arms:
            ax = math.cos(angle)
            ay = math.sin(angle)
            vx = x - cx
            vy = y - cy
            along = vx * ax + vy * ay           # signed distance along arm axis
            perp  = abs(-vx * ay + vy * ax)     # perpendicular distance
            if along < 0.05 * length or along > length:
                continue
            perp_f = math.exp(-(perp / width) ** 2)
            tip_f  = math.sin(math.pi * along / length)   # tapers to 0 at tip
            best = max(best, perp_f * tip_f * 0.55)
        return best

    @staticmethod
    def _terrain_from_value(v: float) -> str:
        if v < 0.30:
            return WATER
        if v < 0.37:   # narrowed sand band — more dirt, less sand
            return SAND
        if v < 0.55:
            return DIRT
        return GRASS

    # ------------------------------------------------------------------
    # Pre-computed noise table helpers
    # ------------------------------------------------------------------

    def _precompute_fbm(self, scale_x: float, scale_y: float,
                        res: int, octaves: int) -> list:
        """
        Build a flat (res*res) FBM lookup table.
        Entry [r*res+c] = fbm(c/res*scale_x, r/res*scale_y).
        Cost is O(res²·octaves) rather than O(W·H·octaves).
        """
        table = [0.0] * (res * res)
        amp   = 0.5
        freq  = 1.0
        for _ in range(octaves):
            for r in range(res):
                ny = r / res * scale_y * freq
                base_idx = r * res
                for c in range(res):
                    nx = c / res * scale_x * freq
                    table[base_idx + c] += self._value_noise(nx, ny) * amp
            amp  *= 0.5
            freq *= 2.0
        return table

    @staticmethod
    def _sample_noise(table: list, res: int, px: float, py: float) -> float:
        """Bilinear sample from the pre-computed flat (res×res) FBM table."""
        xi = int(px)
        yi = int(py)
        xf = px - xi
        yf = py - yi
        xi0 = max(0, min(res - 1, xi))
        yi0 = max(0, min(res - 1, yi))
        xi1 = min(res - 1, xi0 + 1)
        yi1 = min(res - 1, yi0 + 1)
        v00 = table[yi0 * res + xi0]
        v10 = table[yi0 * res + xi1]
        v01 = table[yi1 * res + xi0]
        v11 = table[yi1 * res + xi1]
        sx  = xf * xf * (3.0 - 2.0 * xf)
        sy  = yf * yf * (3.0 - 2.0 * yf)
        top = v00 + sx * (v10 - v00)
        bot = v01 + sx * (v11 - v01)
        return top + sy * (bot - top)

    # ------------------------------------------------------------------
    # Noise implementation (no external dependencies)
    # ------------------------------------------------------------------

    def _fbm(self, x: float, y: float, octaves: int = 4) -> float:
        """Fractional Brownian Motion: sum of value-noise octaves."""
        value     = 0.0
        amplitude = 0.5
        frequency = 1.0
        for _ in range(octaves):
            value     += self._value_noise(x * frequency, y * frequency) * amplitude
            amplitude *= 0.5
            frequency *= 2.0
        return value

    def _value_noise(self, x: float, y: float) -> float:
        """Bilinear value noise with smoothstep interpolation."""
        xi = int(math.floor(x))
        yi = int(math.floor(y))
        xf = x - xi
        yf = y - yi

        v00 = self._hash(xi,     yi)
        v10 = self._hash(xi + 1, yi)
        v01 = self._hash(xi,     yi + 1)
        v11 = self._hash(xi + 1, yi + 1)

        sx = self._smoothstep(xf)
        sy = self._smoothstep(yf)

        top    = v00 + sx * (v10 - v00)
        bottom = v01 + sx * (v11 - v01)
        return top + sy * (bottom - top)

    @staticmethod
    def _smoothstep(t: float) -> float:
        return t * t * (3.0 - 2.0 * t)

    def _hash(self, x: int, y: int) -> float:
        """Deterministic pseudo-random float in [0, 1] for grid cell (x, y)."""
        h = (x * 374_761_393 + y * 668_265_263 + self.seed) & 0xFFFF_FFFF
        h = ((h ^ (h >> 13)) * 1_274_126_177) & 0xFFFF_FFFF
        return (h & 0xFFFF) / 65_535.0


# ---------------------------------------------------------------------------
# Post-generation grass flood fill
# ---------------------------------------------------------------------------

def flood_fill_grass(grid: list[list[str]]) -> list[list[str]]:
    """
    Spread grass into every dirt tile reachable from any starting grass tile
    via 4-connectivity.  Called once right after generation so the island
    interior starts lush and green instead of mostly dirt.
    """
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    queue: deque[tuple[int, int]] = deque()
    seen:  set[tuple[int, int]]   = set()

    for r in range(rows):
        for c in range(cols):
            if grid[r][c] == GRASS:
                pos = (r, c)
                if pos not in seen:
                    seen.add(pos)
                    queue.append(pos)

    while queue:
        r, c = queue.popleft()
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = r + dr, c + dc
            pos2 = (nr, nc)
            if (0 <= nr < rows and 0 <= nc < cols
                    and pos2 not in seen
                    and grid[nr][nc] == DIRT):
                seen.add(pos2)
                grid[nr][nc] = GRASS
                queue.append(pos2)

    return grid


# ---------------------------------------------------------------------------
# Continent generator
# ---------------------------------------------------------------------------

class ContinentGenerator(MapGenerator):
    """
    Generates a large (2048×2048) continent map.

    Features compared to the standard island:
      • Elliptical landmass — wider than tall for a horizontal continent
      • 3–7 peninsula arms (longer and wider than island peninsulas)
      • 2–5 offshore island blobs scattered in the surrounding ocean
      • 6-octave FBM noise for more organic coastlines
    """

    CONTINENT_W = 4096
    CONTINENT_H = 4096

    def __init__(self, seed: int = None):
        super().__init__(width=self.CONTINENT_W, height=self.CONTINENT_H, seed=seed)
        self._cont_cx:  float = 0.0
        self._cont_cy:  float = 0.0
        self._cont_rx:  float = 0.0
        self._cont_ry:  float = 0.0
        self._offshore: list  = []   # [(ix, iy, ir), ...]

    def generate(self) -> list[list[str]]:
        rng = random.Random(self.seed ^ 0xC0FF_EE00)

        # Continent centre — slightly off-centre for asymmetric ocean feel
        self._cont_cx = self.width  * rng.uniform(0.43, 0.52)
        self._cont_cy = self.height * rng.uniform(0.44, 0.56)
        # Elliptical radii — wider than tall
        self._cont_rx = self.width  * rng.uniform(0.27, 0.35)
        self._cont_ry = self.height * rng.uniform(0.23, 0.31)

        max_r = max(self._cont_rx, self._cont_ry)

        # Peninsula arms (more and longer than regular island arms)
        n_arms = rng.randint(3, 7)
        self._arms = []
        for _ in range(n_arms):
            angle  = rng.uniform(0, 2 * math.pi)
            length = rng.uniform(0.48, 1.05) * max_r
            width  = rng.uniform(0.07, 0.18) * max_r
            self._arms.append((angle, length, width))

        # Offshore island blobs
        n_islands = rng.randint(10, 18)
        self._offshore = []
        for _ in range(n_islands):
            angle = rng.uniform(0, 2 * math.pi)
            dist  = rng.uniform(1.15, 2.20) * max_r
            ix = self._cont_cx + math.cos(angle) * dist
            iy = self._cont_cy + math.sin(angle) * dist
            # Clamp so islands don't fall off the grid edge
            margin = 120
            ix = max(margin, min(self.width  - margin, ix))
            iy = max(margin, min(self.height - margin, iy))
            ir = rng.uniform(0.03, 0.10) * max_r
            self._offshore.append((ix, iy, ir))

        # ----------------------------------------------------------------
        # Pre-compute spatial contribution tables at low resolution.
        # Compute terrain values at _COMPUTE_RES, then scale to full size.
        # ----------------------------------------------------------------
        _NR = self._NOISE_RES
        _CR = self._COMPUTE_RES

        noise_tbl      = self._precompute_fbm(4.0, 4.0, _NR, 6)
        radial_tbl     = self._precompute_table(_NR, self._radial_at)
        arm_tbl        = self._precompute_table(_NR, self._arm_at)
        isle_noise_tbl = self._precompute_fbm(9.0, 9.0, _NR, 3)
        isle_tbl       = self._precompute_table_2(_NR, isle_noise_tbl, self._isle_at)

        terrain_lr = []
        for r in range(_CR):
            row = []
            py  = r / _CR * _NR
            for c in range(_CR):
                px     = c / _CR * _NR
                noise  = self._sample_noise(noise_tbl,  _NR, px, py)
                radial = self._sample_noise(radial_tbl, _NR, px, py)
                arm    = self._sample_noise(arm_tbl,    _NR, px, py)
                isle   = self._sample_noise(isle_tbl,   _NR, px, py)
                v      = min(1.0, radial * 0.56 + noise * 0.44 + arm + isle)
                row.append(self._terrain_from_value(v))
            terrain_lr.append(row)

        self.grid = self._scale_terrain(terrain_lr, _CR, self.width, self.height)
        return self.grid

    # Pre-compute helpers
    def _precompute_table(self, res: int, fn) -> list:
        """Build flat (res*res) table where entry = fn(x_frac, y_frac)."""
        W, H = self.width, self.height
        tbl  = [0.0] * (res * res)
        for r in range(res):
            y = r / res * H
            bi = r * res
            for c in range(res):
                x = c / res * W
                tbl[bi + c] = fn(x, y)
        return tbl

    def _precompute_table_2(self, res: int, noise_tbl: list, fn) -> list:
        """Like _precompute_table but also passes the pre-computed noise value."""
        W, H = self.width, self.height
        tbl  = [0.0] * (res * res)
        for r in range(res):
            y = r / res * H
            bi = r * res
            for c in range(res):
                x   = c / res * W
                n   = noise_tbl[bi + c]
                tbl[bi + c] = fn(x, y, n)
        return tbl

    def _radial_at(self, x: float, y: float) -> float:
        dx = (x - self._cont_cx) / self._cont_rx
        dy = (y - self._cont_cy) / self._cont_ry
        return max(0.0, 1.0 - math.sqrt(dx * dx + dy * dy))

    def _arm_at(self, x: float, y: float) -> float:
        return self._arm_contribution(int(x), int(y),
                                      self._cont_cx, self._cont_cy)

    def _isle_at(self, x: float, y: float, inoise: float) -> float:
        best = 0.0
        for ix, iy, ir in self._offshore:
            ddx = (x - ix) / ir
            ddy = (y - iy) / ir
            d   = math.sqrt(ddx * ddx + ddy * ddy)
            if d < 2.0:
                iv = max(0.0, 1.0 - d) * 0.82 + inoise * 0.18
                if iv > best:
                    best = iv
        return best

    def _island_value(self, x: int, y: int,
                      cx: float, cy: float, _max_dist: float) -> float:
        """Legacy per-pixel path (kept for API compat; not called by generate)."""
        dx = (x - cx) / self._cont_rx
        dy = (y - cy) / self._cont_ry
        dist   = math.sqrt(dx * dx + dy * dy)
        radial = max(0.0, 1.0 - dist)
        nx = x / self.width  * 4.0
        ny = y / self.height * 4.0
        noise  = self._fbm(nx, ny, octaves=6)
        base   = radial * 0.56 + noise * 0.44
        arm    = self._arm_contribution(x, y, cx, cy)
        isle   = 0.0
        for ix, iy, ir in self._offshore:
            ddx = (x - ix) / ir
            ddy = (y - iy) / ir
            d = math.sqrt(ddx * ddx + ddy * ddy)
            if d < 2.0:
                iv = max(0.0, 1.0 - d) * 0.82
                inx = x / self.width  * 9.0 + ix * 0.004
                iny = y / self.height * 9.0 + iy * 0.004
                iv += self._fbm(inx, iny, octaves=3) * 0.18
                isle = max(isle, iv)
        return min(1.0, base + arm + isle)

    def _continent_value_fast(self, x: int, y: int,
                              cx: float, cy: float,
                              noise: float, inoise: float) -> float:
        """Fast path: both noise values are already pre-sampled."""
        dx = (x - cx) / self._cont_rx
        dy = (y - cy) / self._cont_ry
        dist   = math.sqrt(dx * dx + dy * dy)
        radial = max(0.0, 1.0 - dist)
        base   = radial * 0.56 + noise * 0.44
        arm    = self._arm_contribution(x, y, cx, cy)
        isle   = 0.0
        for ix, iy, ir in self._offshore:
            ddx = (x - ix) / ir
            ddy = (y - iy) / ir
            d = math.sqrt(ddx * ddx + ddy * ddy)
            if d < 2.0:
                iv = max(0.0, 1.0 - d) * 0.82 + inoise * 0.18
                isle = max(isle, iv)
        return min(1.0, base + arm + isle)
