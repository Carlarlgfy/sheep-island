"""
Downscale wolf sprites from their native 1024/1536 px to 384 px max dimension.
Run once before launching the game to reduce VRAM/RAM usage.
Overwrites the originals in-place (they are generated assets).
"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
pygame.init()
# Need a tiny display surface so convert_alpha() works
pygame.display.set_mode((1, 1))

WOLF_DIR = os.path.join(os.path.dirname(__file__), "brown gray female wolf")
MAX_DIM  = 384

processed = 0
for fname in sorted(os.listdir(WOLF_DIR)):
    if not fname.lower().endswith(".png"):
        continue
    path = os.path.join(WOLF_DIR, fname)
    surf = pygame.image.load(path).convert_alpha()
    w, h = surf.get_size()
    if max(w, h) <= MAX_DIM:
        print(f"  skip (already ≤{MAX_DIM}px): {fname}  [{w}×{h}]")
        continue
    scale = MAX_DIM / max(w, h)
    nw    = max(1, int(w * scale))
    nh    = max(1, int(h * scale))
    resized = pygame.transform.smoothscale(surf, (nw, nh))
    pygame.image.save(resized, path)
    print(f"  {w}×{h} → {nw}×{nh}  {fname}")
    processed += 1

pygame.quit()
print(f"\nDone — {processed} sprite(s) resized.")
