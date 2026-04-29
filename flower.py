"""
Flower system — decorative flowers that sit on grass tiles.

Each Flower has:
  • A tile position (tile_x, tile_y) + a randomised sub-tile offset
  • A type: WHITE, RED, or YELLOW
  • A size factor (fraction of tile_size): between 1/11 and 1/8
  • A slight random rotation so not every flower looks identical
  • 4 circular petals arranged at 90° intervals around a small centre disc
    - White  → white petals, yellow centre
    - Red    → red petals,   yellow centre
    - Yellow → yellow petals, white centre

FlowerManager stores a dict of (tile_x, tile_y) → list[Flower] and handles
bulk drawing with view-frustum culling.
"""

import math
import random

import pygame

# ---------------------------------------------------------------------------
# Flower
# ---------------------------------------------------------------------------

class Flower:
    WHITE  = "white"
    YELLOW = "yellow"
    RED    = "red"

    _PETAL_COLOR = {
        WHITE:  (242, 242, 246),
        YELLOW: (252, 215,  35),
        RED:    (218,  40,  40),
    }
    _CENTER_COLOR = {
        WHITE:  (238, 195,  28),   # yellow stamen
        YELLOW: (246, 246, 243),   # white stamen
        RED:    (235, 192,  22),   # yellow stamen
    }

    # Don't bother drawing when tiles are this small — flowers would be < 1 px
    LOD_MIN_TS = 5.0

    def __init__(self, tile_x: int, tile_y: int, ftype: str):
        self.tile_x = int(tile_x)
        self.tile_y = int(tile_y)
        self.ftype  = ftype
        # Sub-tile placement: avoid corners so petals don't clip tile edges
        self.ox = random.uniform(0.20, 0.80)
        self.oy = random.uniform(0.20, 0.80)
        # Flower radius as a fraction of tile_size (1/11 → 1/8)
        self.size_factor = random.uniform(1.0 / 11.0, 1.0 / 8.0)
        # Small rotation so flowers aren't all perfectly axis-aligned
        self.angle = random.uniform(0.0, math.pi / 4.0)

    # ------------------------------------------------------------------

    def draw(self, screen: pygame.Surface,
             cam_x: float, cam_y: float, tile_size: float):
        if tile_size < self.LOD_MIN_TS:
            return

        # World-pixel centre of this flower
        px = (self.tile_x + self.ox) * tile_size - cam_x
        py = (self.tile_y + self.oy) * tile_size - cam_y

        # Radius of each petal circle (and offset from flower centre)
        r = self.size_factor * tile_size
        petal_r  = max(1, round(r))
        # Centre disc (the stamen / receptacle)
        center_r = max(1, round(r * 0.45))

        petal_color  = self._PETAL_COLOR[self.ftype]
        center_color = self._CENTER_COLOR[self.ftype]

        # Draw 4 petals; each petal circle's centre is 1 petal-radius away
        # from the flower centre along its axis
        for i in range(4):
            a  = self.angle + i * (math.pi / 2.0)
            cx = round(px + math.cos(a) * petal_r)
            cy = round(py + math.sin(a) * petal_r)
            pygame.draw.circle(screen, petal_color, (cx, cy), petal_r)

        # Centre disc drawn on top of petals
        pygame.draw.circle(screen, center_color, (round(px), round(py)), center_r)


# ---------------------------------------------------------------------------
# FlowerManager
# ---------------------------------------------------------------------------

class FlowerManager:
    """Stores all placed flowers and draws only the ones in the current view."""

    def __init__(self):
        # (tile_x, tile_y) → list[Flower]
        self._flowers: dict[tuple[int, int], list[Flower]] = {}

    # ------------------------------------------------------------------
    # Mutation

    def has_flowers(self, tile_x: int, tile_y: int) -> bool:
        return bool(self._flowers.get((int(tile_x), int(tile_y))))

    def add(self, tile_x: int, tile_y: int, ftype: str) -> "Flower | None":
        """Place a flower on a tile.  Returns None if that tile already has flowers."""
        key = (int(tile_x), int(tile_y))
        if key in self._flowers:
            return None   # no stacking — tile already occupied
        f = Flower(tile_x, tile_y, ftype)
        self._flowers[key] = [f]
        return f

    def remove_tile(self, tile_x: int, tile_y: int):
        """Remove all flowers from a tile (e.g. when it stops being grass)."""
        self._flowers.pop((int(tile_x), int(tile_y)), None)

    def clear(self):
        self._flowers.clear()

    # ------------------------------------------------------------------
    # Query

    def flowers_at(self, tile_x: int, tile_y: int) -> list:
        return self._flowers.get((int(tile_x), int(tile_y)), [])

    # ------------------------------------------------------------------
    # Drawing

    def draw_all(self, screen: pygame.Surface,
                 cam_x: float, cam_y: float,
                 tile_size: float, screen_w: int, screen_h: int):
        if tile_size < Flower.LOD_MIN_TS or not self._flowers:
            return

        # Cull to visible tile range
        start_c = max(0, int(math.floor(cam_x / tile_size)) - 1)
        start_r = max(0, int(math.floor(cam_y / tile_size)) - 1)
        end_c   = int(math.ceil((cam_x + screen_w) / tile_size)) + 1
        end_r   = int(math.ceil((cam_y + screen_h) / tile_size)) + 1

        for tr in range(start_r, end_r + 1):
            for tc in range(start_c, end_c + 1):
                flowers = self._flowers.get((tc, tr))
                if flowers:
                    for f in flowers:
                        f.draw(screen, cam_x, cam_y, tile_size)
