import random
import math

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

    def generate(self) -> list[list[str]]:
        cx = self.width  / 2.0
        cy = self.height / 2.0
        max_dist = min(cx, cy) * 0.60  # island uses 60% of radius, leaving water border

        self.grid = []
        for y in range(self.height):
            row = []
            for x in range(self.width):
                island_val = self._island_value(x, y, cx, cy, max_dist)
                row.append(self._terrain_from_value(island_val))
            self.grid.append(row)

        return self.grid

    def get_tile(self, x: int, y: int) -> str:
        """Returns terrain at (x, y). Out-of-bounds tiles are treated as water."""
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.grid[y][x]
        return WATER

    def get_neighbors(self, x: int, y: int) -> dict[str, str]:
        """
        Returns all 8 cardinal + diagonal neighbor terrain types.
        Keys: N, NE, E, SE, S, SW, W, NW
        Useful for choosing the correct transition sprite later.
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
        """
        Returns a 0-1 float where higher = more inland (grass),
        lower = more coastal / open water.
        """
        # Radial gradient: 1.0 at center, 0.0 at max_dist radius
        dx = (x - cx) / max_dist
        dy = (y - cy) / max_dist
        dist     = math.sqrt(dx * dx + dy * dy)
        radial   = max(0.0, 1.0 - dist)

        # FBM noise sampled at a gentle scale for organic coastline variation
        nx = x / self.width  * 4.5
        ny = y / self.height * 4.5
        noise = self._fbm(nx, ny, octaves=5)

        # Blend: radial gradient dominates shape, noise distorts the edges
        return radial * 0.62 + noise * 0.38

    @staticmethod
    def _terrain_from_value(v: float) -> str:
        if v < 0.30:
            return WATER
        if v < 0.42:
            return SAND
        if v < 0.55:
            return DIRT
        return GRASS

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
