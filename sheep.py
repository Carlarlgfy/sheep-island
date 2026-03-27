import pygame
import math
import random
import os

from mapgen import WATER

_SHEEP_DIR = os.path.join(os.path.dirname(__file__), "sheep experiment")


class Sheep:
    IDLE  = "idle"
    WALK  = "walk"

    SPEED = 1.5  # tiles per second while walking

    _sprites_raw: dict | None = None
    _cache: dict = {}

    def __init__(self, tile_x: float, tile_y: float):
        self.tx = float(tile_x)
        self.ty = float(tile_y)
        self.dx = 0.0
        self.dy = 0.0
        self.facing = "front"
        self.state   = Sheep.IDLE
        self.timer   = 0.0
        self._schedule_idle()

    # ------------------------------------------------------------------
    # Sprite loading and per-tile-size caching
    # ------------------------------------------------------------------

    @classmethod
    def load_sprites(cls):
        if cls._sprites_raw is not None:
            return
        front  = pygame.image.load(os.path.join(_SHEEP_DIR, "Front_Facing.png")).convert_alpha()
        behind = pygame.image.load(os.path.join(_SHEEP_DIR, "Behind_Facing.png")).convert_alpha()
        right  = pygame.image.load(os.path.join(_SHEEP_DIR, "Right_Facing.png")).convert_alpha()
        left   = pygame.transform.flip(right, True, False)
        cls._sprites_raw = {"front": front, "behind": behind, "right": right, "left": left}
        cls._cache = {}

    @classmethod
    def _scaled(cls, facing: str, tile_size: float) -> pygame.Surface:
        ts = max(1, round(tile_size))
        if ts not in cls._cache:
            target_h = max(8, ts * 2)
            entry = {}
            for k, surf in cls._sprites_raw.items():
                ow, oh = surf.get_size()
                nw = max(1, int(ow * target_h / oh))
                entry[k] = pygame.transform.scale(surf, (nw, target_h))
            cls._cache[ts] = entry
        return cls._cache[ts][facing]

    # ------------------------------------------------------------------
    # State scheduling
    # ------------------------------------------------------------------

    def _schedule_idle(self):
        self.state = Sheep.IDLE
        self.dx    = 0.0
        self.dy    = 0.0
        self.timer = random.uniform(2.0, 5.0)

    def _schedule_walk(self):
        self.state = Sheep.WALK
        self.timer = random.uniform(1.5, 4.0)
        angle = random.uniform(0, 2 * math.pi)
        self.dx = math.cos(angle)
        self.dy = math.sin(angle)
        self._refresh_facing()

    def _refresh_facing(self):
        if abs(self.dx) >= abs(self.dy):
            self.facing = "right" if self.dx >= 0 else "left"
        else:
            self.facing = "front" if self.dy > 0 else "behind"

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(self, dt: float, grid: list):
        self.timer -= dt

        if self.state == Sheep.IDLE:
            if self.timer <= 0:
                self._schedule_walk()
            return

        # --- WALK ---
        if self.timer <= 0:
            self._schedule_idle()
            return

        new_tx = self.tx + self.dx * self.SPEED * dt
        new_ty = self.ty + self.dy * self.SPEED * dt

        col  = int(new_tx)
        row  = int(new_ty)
        rows = len(grid)
        cols = len(grid[0]) if rows else 0

        if 0 <= row < rows and 0 <= col < cols and grid[row][col] != WATER:
            self.tx = new_tx
            self.ty = new_ty
        else:
            # Hit water or boundary — pause briefly before re-wandering
            self.state = Sheep.IDLE
            self.dx    = 0.0
            self.dy    = 0.0
            self.timer = random.uniform(0.3, 1.0)

    # ------------------------------------------------------------------
    # Draw
    # ------------------------------------------------------------------

    def draw(self, screen: pygame.Surface, cam_x: float, cam_y: float, tile_size: float):
        sprite = self._scaled(self.facing, tile_size)
        ts = max(1, round(tile_size))
        w, h = sprite.get_size()
        sx = int(self.tx * ts - cam_x) - w // 2
        sy = int(self.ty * ts - cam_y) - h // 2
        screen.blit(sprite, (sx, sy))
