import pygame
import random
from mapgen import WATER, SAND, DIRT, GRASS

# ---------------------------------------------------------------------------
# Base colours — slightly richer / darker than the old flat values
# ---------------------------------------------------------------------------

BASE_COLORS = {
    WATER: ( 38, 100, 182),
    SAND:  (198, 176, 112),
    DIRT:  (115,  82,  44),
    GRASS: ( 48, 108,  32),
}

# Exported so main.py can use it for the background fill
WATER_COLOR = BASE_COLORS[WATER]

# Elevation priority: higher number = visually "raised" above lower numbers.
# Shadow strips are drawn on the high-terrain side of any high→low border.
_PRIORITY = {WATER: 0, SAND: 1, DIRT: 2, GRASS: 3}

# Per-terrain brightness variation range (fraction of base channel value)
_SHADE_VAR = {WATER: 0.08, SAND: 0.13, DIRT: 0.11, GRASS: 0.14}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clamp(v: int) -> int:
    return max(0, min(255, v))


def _h(x: int, y: int, s: int = 0) -> int:
    """Deterministic, position-based 32-bit hash (no RNG, no flicker)."""
    v = (x * 374_761_393 + y * 668_265_263 + s) & 0xFFFF_FFFF
    v = ((v ^ (v >> 13)) * 1_274_126_177) & 0xFFFF_FFFF
    return v


def _darken(c: tuple, amt: int) -> tuple:
    return (_clamp(c[0] - amt), _clamp(c[1] - amt), _clamp(c[2] - amt))


def _lighten(c: tuple, amt: int) -> tuple:
    return (_clamp(c[0] + amt), _clamp(c[1] + amt), _clamp(c[2] + amt))


def _blend(a: tuple, b: tuple, t: float) -> tuple:
    """Linear blend between two RGB tuples. t=0 → a, t=1 → b."""
    return (
        _clamp(int(a[0] + (b[0] - a[0]) * t)),
        _clamp(int(a[1] + (b[1] - a[1]) * t)),
        _clamp(int(a[2] + (b[2] - a[2]) * t)),
    )


# ---------------------------------------------------------------------------
# TerrainRenderer
# ---------------------------------------------------------------------------

class TerrainRenderer:
    """
    Replaces the flat draw_map loop.

    Features:
      • Per-tile brightness variation (deterministic, position-based hash)
      • Sub-tile texture marks for grass, dirt, sand, water (when tiles ≥ 8 px)
      • Edge-shadow strips where high terrain meets low (when tiles ≥ 3 px)
      • Foam highlight on sand→water border
      • Shallow-water tint on water→sand border
      • Cached 1-pixel-per-tile surface for fast low-zoom rendering

    Call update(dt) each frame.
    Call draw(screen, tile_size, cam_x, cam_y, screen_w, screen_h) to render.
    """

    # Below this tile size the cached surface path is used (no sub-tile detail needed)
    _LOW_ZOOM_THRESHOLD = 8

    def __init__(self, grid: list[list[str]]):
        self.grid = grid          # mutable reference — changes (eaten grass etc.) auto-reflect
        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        # Pre-compute [0, 1] shade per tile (doesn't depend on terrain type)
        self._shade = [
            [(_h(c, r) & 0xFFFF) / 65_535.0 for c in range(cols)]
            for r in range(rows)
        ]
        self._wave_t = 0.0        # future water animation timer

        # --- Cached 1px-per-tile surface for low-zoom rendering ---
        self._map_surf: pygame.Surface | None = None
        self._dirty: set[tuple[int, int]] = set()

    # ------------------------------------------------------------------
    # Cached surface helpers
    # ------------------------------------------------------------------

    def mark_dirty(self, row: int, col: int):
        """Call whenever a tile's terrain type changes (eaten, regrown, etc.)."""
        self._dirty.add((row, col))

    def _build_map_surf(self):
        """Build the 1px-per-tile cached surface using run-length encoded rows."""
        rows = len(self.grid)
        cols = len(self.grid[0]) if rows else 0
        surf = pygame.Surface((cols, rows))
        for r in range(rows):
            row_data = self.grid[r]
            c_start  = 0
            cur_col  = BASE_COLORS[row_data[0]]
            for c in range(1, cols):
                col_color = BASE_COLORS[row_data[c]]
                if col_color != cur_col:
                    pygame.draw.rect(surf, cur_col, (c_start, r, c - c_start, 1))
                    c_start  = c
                    cur_col  = col_color
            pygame.draw.rect(surf, cur_col, (c_start, r, cols - c_start, 1))
        self._map_surf = surf

    def _draw_cached(self, screen: pygame.Surface,
                     cam_x: float, cam_y: float,
                     screen_w: int, screen_h: int,
                     rows: int, cols: int, ts: int):
        """Blit a scaled crop of the cached map surface — O(1) draw calls."""
        start_c = max(0, int(cam_x) // ts)
        start_r = max(0, int(cam_y) // ts)
        end_c   = min(cols, start_c + screen_w // ts + 2)
        end_r   = min(rows, start_r + screen_h // ts + 2)
        src_w   = end_c - start_c
        src_h   = end_r - start_r
        if src_w <= 0 or src_h <= 0:
            return
        src_rect = pygame.Rect(start_c, start_r, src_w, src_h)
        sub      = self._map_surf.subsurface(src_rect)
        scaled   = pygame.transform.scale(sub, (src_w * ts, src_h * ts))
        screen.blit(scaled, (start_c * ts - int(cam_x), start_r * ts - int(cam_y)))

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def update(self, dt: float):
        self._wave_t += dt
        # Apply pending tile changes to the cached surface
        if self._map_surf is not None and self._dirty:
            rows = len(self.grid)
            cols = len(self.grid[0]) if rows else 0
            for (r, c) in self._dirty:
                if 0 <= r < rows and 0 <= c < cols:
                    self._map_surf.set_at((c, r), BASE_COLORS[self.grid[r][c]])
            self._dirty.clear()

    # ------------------------------------------------------------------
    # Main draw
    # ------------------------------------------------------------------

    def draw(self, screen: pygame.Surface, tile_size: float,
             cam_x: float, cam_y: float, screen_w: int, screen_h: int):
        grid = self.grid
        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        ts   = max(1, round(tile_size))
        cx   = int(cam_x)
        cy   = int(cam_y)

        # --- Fast path: cached surface for small tile sizes ---
        if ts < self._LOW_ZOOM_THRESHOLD:
            if self._map_surf is None:
                self._build_map_surf()
            self._draw_cached(screen, cam_x, cam_y, screen_w, screen_h, rows, cols, ts)
            return

        start_col = max(0, cx // ts)
        start_row = max(0, cy // ts)
        end_col   = min(cols, start_col + screen_w // ts + 2)
        end_row   = min(rows, start_row + screen_h // ts + 2)

        do_texture = ts >= 8
        do_borders = ts >= 3

        for row in range(start_row, end_row):
            for col in range(start_col, end_col):
                terrain = grid[row][col]
                sx = col * ts - cx
                sy = row * ts - cy
                color = self._tile_color(terrain, col, row)

                pygame.draw.rect(screen, color, (sx, sy, ts, ts))

                if do_texture:
                    self._draw_texture(screen, terrain, col, row, sx, sy, ts)

                if do_borders:
                    self._draw_borders(screen, grid, terrain, col, row,
                                       sx, sy, ts, rows, cols, color)

    # ------------------------------------------------------------------
    # Colour helpers
    # ------------------------------------------------------------------

    def _tile_color(self, terrain: str, col: int, row: int) -> tuple:
        base  = BASE_COLORS[terrain]
        shade = self._shade[row][col]
        var   = _SHADE_VAR[terrain]
        f     = 1.0 + (shade - 0.5) * 2.0 * var
        return (_clamp(int(base[0] * f)),
                _clamp(int(base[1] * f)),
                _clamp(int(base[2] * f)))

    # ------------------------------------------------------------------
    # Sub-tile textures
    # ------------------------------------------------------------------

    def _draw_texture(self, screen, terrain, col, row, sx, sy, ts):
        if   terrain == GRASS: self._tex_grass(screen, col, row, sx, sy, ts)
        elif terrain == DIRT:  self._tex_dirt (screen, col, row, sx, sy, ts)
        elif terrain == SAND:  self._tex_sand (screen, col, row, sx, sy, ts)
        elif terrain == WATER: self._tex_water(screen, col, row, sx, sy, ts)

    def _tex_grass(self, screen, col, row, sx, sy, ts):
        """2–3 dark blade marks + 1 bright highlight per tile."""
        ts1 = max(1, ts - 1)
        shade = self._shade[row][col]

        for i in range(3):
            h  = _h(col * 7 + i, row * 13 + i)
            px = sx + (h >> 4)  % ts1
            py = sy + (h >> 12) % ts1
            # Vary blade darkness a little with per-tile shade
            dark = (_clamp(18 + int(shade * 12)),
                    _clamp(72 + int(shade * 16)),
                    _clamp( 8 + int(shade *  8)))
            pygame.draw.rect(screen, dark, (px, py, 1, 1 + (h >> 22) % 2))

        # Light highlight (dew / top of blade)
        h2 = _h(col * 17 + 99, row * 23)
        pygame.draw.rect(screen, (88, 162, 58),
                         (sx + (h2 >> 4) % ts1, sy + (h2 >> 12) % ts1, 1, 1))

    def _tex_dirt(self, screen, col, row, sx, sy, ts):
        """Scattered pebble-like marks — dark stones + pale clay spots."""
        ts1 = max(1, ts - 1)
        for i in range(3):
            h  = _h(col * 11 + i, row * 7 + i)
            px = sx + (h >> 4)  % ts1
            py = sy + (h >> 12) % ts1
            if (h >> 20) & 1:
                pygame.draw.rect(screen, ( 85, 58, 26), (px, py, 1, 1))   # dark stone
            else:
                pygame.draw.rect(screen, (148, 108, 66), (px, py, 1, 1))  # pale clay

    def _tex_sand(self, screen, col, row, sx, sy, ts):
        """Grainy texture — 5 fine bright/dark grain dots per tile."""
        ts1 = max(1, ts - 1)
        for i in range(5):
            h  = _h(col * 5 + i, row * 9 + i, i + 1)
            px = sx + (h >> 4)  % ts1
            py = sy + (h >> 12) % ts1
            if (h >> 20) & 1:
                pygame.draw.rect(screen, (228, 210, 150), (px, py, 1, 1))  # bright grain
            else:
                pygame.draw.rect(screen, (175, 154,  92), (px, py, 1, 1))  # dark grain

    def _tex_water(self, screen, col, row, sx, sy, ts):
        """Subtle diagonal ripple stripe — no flicker, no RNG."""
        if ts < 12:
            return
        # Ripple runs diagonally: tiles where (col + row) hits a certain band
        phase = (col + row) % 6
        if phase not in (0, 1):
            return
        ripple_y = sy + ts // 3 + (1 if phase == 1 else 0)
        w = max(2, ts * 2 // 3)
        px = sx + (ts - w) // 2
        pygame.draw.rect(screen, (68, 138, 210), (px, ripple_y, w, 1))

    # ------------------------------------------------------------------
    # Edge blending / border shadows
    # ------------------------------------------------------------------

    def _draw_borders(self, screen, grid, terrain, col, row,
                      sx, sy, ts, rows, cols, base_color):
        prio = _PRIORITY[terrain]
        bw   = max(1, ts // 7)   # strip width scales with tile size

        def nbr(dc, dr):
            c, r = col + dc, row + dr
            if 0 <= r < rows and 0 <= c < cols:
                return grid[r][c]
            return WATER

        # Cardinal edges: (neighbour offset, strip rect relative to tile origin)
        edges = [
            ((0, -1), (sx,           sy,           ts,  bw)),   # N
            ((0,  1), (sx,           sy + ts - bw, ts,  bw)),   # S
            ((-1, 0), (sx,           sy,           bw,  ts)),   # W
            ((1,  0), (sx + ts - bw, sy,           bw,  ts)),   # E
        ]

        for (dc, dr), strip in edges:
            n      = nbr(dc, dr)
            n_prio = _PRIORITY[n]

            if n_prio < prio:
                # This tile is higher terrain — draw inner shadow on this edge
                shadow = _darken(base_color, 34)
                pygame.draw.rect(screen, shadow, strip)

            elif terrain == SAND and n == WATER:
                # Foam / wet-sand highlight at beach edge
                pygame.draw.rect(screen, (230, 218, 168), strip)

            elif terrain == WATER and n == SAND:
                # Shallow pale-blue shimmer at shoreline
                pygame.draw.rect(screen, (65, 135, 205), strip)

            elif terrain == GRASS and n == DIRT:
                # Slightly lighter green fringe where grass overhangs dirt
                fringe = _lighten(base_color, 14)
                pygame.draw.rect(screen, fringe, strip)


# ---------------------------------------------------------------------------
# Grass spreading (frontier BFS)
# ---------------------------------------------------------------------------

# Tiles converted per sim-second at 1× speed.
# Scales with sim speed (3×, 8×) automatically.
_SPREAD_RATE = 8


class GrassSpread:
    """
    Tracks the frontier of dirt tiles adjacent to grass and converts them
    progressively each frame.  Much faster than random-sampling the whole map
    because every candidate is already guaranteed to border existing grass.
    """

    def __init__(self, grid: list[list[str]]):
        self.grid = grid
        self.rows = len(grid)
        self.cols = len(grid[0]) if self.rows else 0
        # Seed the frontier: every dirt tile that touches at least one grass tile
        self.frontier: set[tuple[int, int]] = set()
        for r in range(self.rows):
            for c in range(self.cols):
                if grid[r][c] == DIRT and self._borders_grass(r, c):
                    self.frontier.add((r, c))

    # ------------------------------------------------------------------

    def _borders_grass(self, r: int, c: int) -> bool:
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < self.rows and 0 <= nc < self.cols:
                if self.grid[nr][nc] == GRASS:
                    return True
        return False

    def update(self, dt_sim: float, notify=None):
        if dt_sim <= 0 or not self.frontier:
            return

        n = max(1, int(_SPREAD_RATE * dt_sim))
        candidates = random.sample(list(self.frontier), min(n, len(self.frontier)))

        for (r, c) in candidates:
            if self.grid[r][c] != DIRT:
                self.frontier.discard((r, c))
                continue
            # Convert this tile and add its dirt neighbours to the frontier
            self.grid[r][c] = GRASS
            self.frontier.discard((r, c))
            if notify:
                notify(r, c)
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nr, nc = r + dr, c + dc
                if 0 <= nr < self.rows and 0 <= nc < self.cols:
                    if self.grid[nr][nc] == DIRT:
                        self.frontier.add((nr, nc))
