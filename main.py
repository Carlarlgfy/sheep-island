import pygame
import sys
import random
import math
import threading
import json
import time
from pathlib import Path

from mapgen import (
    MapGenerator, ContinentGenerator, flood_fill_grass,
    WATER, SAND, DIRT, GRASS, WALL, TUNDRA, SNOW,
    is_walkable_tile, is_walkable_terrain,
)
from sheep import Sheep
from ram import Ram
from grass import TerrainRenderer, GrassSpread, TundraSpread, WATER_COLOR
from flower import Flower, FlowerManager
from herd import HerdManager
from wolf import Wolf
from wolf_pack import WolfPackManager
from scanning import ProximityScanner

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ISLAND_W, ISLAND_H         = 1024, 1024
CONTINENT_W, CONTINENT_H   = 4096, 4096

TILE_SIZE_DEFAULT           = 14.0
CONTINENT_TILE_DEFAULT      = 4.0
TILE_SIZE_MIN               = 0.5
TILE_SIZE_MAX               = 48.0
ZOOM_FACTOR                 = 1.1
CAMERA_SPEED                = 28

STATE_TITLE      = "title"
STATE_START_GAME = "start_game"
STATE_MAP_SELECT = "map_select"
STATE_LOADING    = "loading"
STATE_PLAY       = "play"
STATE_CHARACTER_CREATOR = "character_creator"

DAY_CYCLE_DURATION = 300.0

BOTTOM_BAR_H = 48

SPEED_SCALES = [0, 1, 3, 8]
SPEED_LABELS = ["||", ">", ">>", ">>>"]

HERD_UPDATE_STEP = 0.25
TERRAIN_SPREAD_STEP = 0.50

CHARACTER_SAVE_PATH = Path("characters.json")

CHARACTER_OPTIONS = {
    "body": ["Slim", "Average", "Broad"],
    "head": ["Round", "Square", "Long"],
    "eyes": ["Brown", "Blue", "Green", "Gray"],
    "hair": ["Short", "Bob", "Long", "Bald"],
    "hair_color": ["Black", "Brown", "Blond", "Red", "Gray"],
    "skin": ["Light", "Tan", "Brown", "Dark"],
    "difficulty": ["Easy", "Medium", "Hard"],
}

SKIN_COLORS = {
    "Light": (232, 188, 145),
    "Tan": (196, 134, 84),
    "Brown": (132, 82, 52),
    "Dark": (82, 50, 35),
}

HAIR_COLORS = {
    "Black": (24, 22, 20),
    "Brown": (76, 48, 28),
    "Blond": (206, 168, 70),
    "Red": (150, 64, 38),
    "Gray": (120, 120, 112),
}

EYE_COLORS = {
    "Brown": (70, 42, 24),
    "Blue": (54, 112, 170),
    "Green": (56, 124, 70),
    "Gray": (118, 128, 132),
}

# Spawner mode keys
SPAWN_FEMALE_SHEEP = "female_sheep"
SPAWN_MALE_SHEEP   = "male_sheep"
SPAWN_FEMALE_WOLF  = "female_wolf"
SPAWN_MALE_WOLF    = "male_wolf"

SPAWNER_LABELS = {
    SPAWN_FEMALE_SHEEP: "Fem. Sheep",
    SPAWN_MALE_SHEEP:   "Ram",
    SPAWN_FEMALE_WOLF:  "Fem. Wolf",
    SPAWN_MALE_WOLF:    "Wolf",
}
SPAWNER_COLORS = {
    SPAWN_FEMALE_SHEEP: (50,  150,  70),
    SPAWN_MALE_SHEEP:   (60,  120, 190),
    SPAWN_FEMALE_WOLF:  (190,  65, 120),
    SPAWN_MALE_WOLF:    (170,  50,  50),
}

TERRAIN_PAINT_COLORS = {
    WATER:  ( 38, 100, 182),
    SAND:   (170, 150,  80),
    DIRT:   (100,  70,  30),
    GRASS:  ( 40, 100,  25),
    WALL:   (150,  78,  60),
    TUNDRA: (142, 136, 122),
    SNOW:   (210, 220, 230),
}


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def draw_button(surface, btn, font):
    pygame.draw.rect(surface, btn["color"], btn["rect"], border_radius=8)
    label = font.render(btn["label"], True, (255, 255, 255))
    surface.blit(label, label.get_rect(center=btn["rect"].center))


def draw_title(screen, font_title, font_ui, buttons, screen_w, screen_h):
    screen.fill((18, 18, 36))
    title_surf = font_title.render("Sheep Island", True, (240, 215, 90))
    screen.blit(title_surf, title_surf.get_rect(center=(screen_w // 2, screen_h // 3)))
    sub = font_ui.render("a procedural island simulator", True, (140, 140, 180))
    screen.blit(sub, sub.get_rect(center=(screen_w // 2, screen_h // 3 + 64)))
    for btn in buttons:
        draw_button(screen, btn, font_ui)


def draw_map_select(screen, font_title, font_ui, buttons, screen_w, screen_h):
    """Map type selection screen shown after choosing sandbox simulation."""
    screen.fill((18, 18, 36))
    title_surf = font_title.render("Select Map Type", True, (240, 215, 90))
    screen.blit(title_surf, title_surf.get_rect(center=(screen_w // 2, screen_h // 4)))

    # Descriptions above each generate button
    island_desc = font_ui.render(
        "Island — compact randomized island with optional peninsulas  (1024 x 1024)",
        True, (140, 140, 180))
    screen.blit(island_desc,
                island_desc.get_rect(center=(screen_w // 2, screen_h // 2 - 18)))

    continent_desc = font_ui.render(
        "Continent — large elliptical landmass, many peninsulas and offshore islands  (2048 x 2048)",
        True, (140, 140, 180))
    screen.blit(continent_desc,
                continent_desc.get_rect(center=(screen_w // 2, screen_h // 2 + 80)))

    for btn in buttons:
        draw_button(screen, btn, font_ui)


def draw_start_game(screen, font_title, font_ui, buttons, screen_w, screen_h):
    screen.fill((18, 18, 36))
    title_surf = font_title.render("Start Game", True, (240, 215, 90))
    screen.blit(title_surf, title_surf.get_rect(center=(screen_w // 2, screen_h // 4)))
    sub = font_ui.render("campaign and saves are placeholders for now", True, (140, 140, 180))
    screen.blit(sub, sub.get_rect(center=(screen_w // 2, screen_h // 4 + 56)))
    for btn in buttons:
        draw_button(screen, btn, font_ui)


def draw_loading(screen, font_title, font_ui, dot_count, sheep_surf,
                 sheep_px, sheep_py, screen_w, screen_h, gen_type="island"):
    screen.fill((18, 18, 36))
    dots = "." * (dot_count + 1)
    label = font_title.render(f"Loading{dots}", True, (240, 215, 90))
    screen.blit(label, label.get_rect(center=(screen_w // 2, screen_h // 2 - 40)))
    hint_text = f"generating {'continent' if gen_type == 'continent' else 'island'}\u2026"
    hint = font_ui.render(hint_text, True, (100, 100, 140))
    screen.blit(hint, hint.get_rect(center=(screen_w // 2, screen_h // 2 + 30)))
    if sheep_surf is not None:
        w, h = sheep_surf.get_size()
        screen.blit(sheep_surf, (int(sheep_px) - w // 2, int(sheep_py) - h // 2))


def load_character_library():
    try:
        with CHARACTER_SAVE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return [c for c in data if isinstance(c, dict)]


def save_character_library(characters):
    with CHARACTER_SAVE_PATH.open("w", encoding="utf-8") as f:
        json.dump(characters, f, indent=2)


def make_blank_character():
    return {
        "name": "Grug",
        "body": "Average",
        "head": "Round",
        "eyes": "Brown",
        "hair": "Short",
        "hair_color": "Black",
        "skin": "Light",
        "difficulty": "Easy",
    }


def cycle_character_option(character, key, direction):
    options = CHARACTER_OPTIONS[key]
    cur = character.get(key, options[0])
    idx = options.index(cur) if cur in options else 0
    character[key] = options[(idx + direction) % len(options)]


def draw_character_preview(screen, character, area_rect):
    pygame.draw.rect(screen, (26, 30, 36), area_rect, border_radius=8)
    pygame.draw.rect(screen, (88, 98, 112), area_rect, width=2, border_radius=8)

    cx = area_rect.centerx
    base_y = area_rect.bottom - 80
    skin = SKIN_COLORS.get(character.get("skin"), SKIN_COLORS["Tan"])
    hair_col = HAIR_COLORS.get(character.get("hair_color"), HAIR_COLORS["Brown"])
    eye_col = EYE_COLORS.get(character.get("eyes"), EYE_COLORS["Brown"])

    body = character.get("body", "Average")
    if body == "Slim":
        torso_w, torso_h = 72, 155
    elif body == "Broad":
        torso_w, torso_h = 112, 150
    else:
        torso_w, torso_h = 92, 152

    torso = pygame.Rect(0, 0, torso_w, torso_h)
    torso.midbottom = (cx, base_y)
    pygame.draw.rect(screen, (73, 88, 106), torso, border_radius=16)
    pygame.draw.rect(screen, (47, 56, 68), torso, width=3, border_radius=16)
    pygame.draw.line(screen, (42, 49, 58), (cx, torso.top + 18), (cx, torso.bottom - 8), 2)

    leg_w = max(24, torso_w // 3)
    left_leg = pygame.Rect(cx - leg_w - 6, torso.bottom - 3, leg_w, 62)
    right_leg = pygame.Rect(cx + 6, torso.bottom - 3, leg_w, 62)
    for leg in (left_leg, right_leg):
        pygame.draw.rect(screen, (47, 57, 70), leg, border_radius=8)

    arm_w = 22
    left_arm = pygame.Rect(torso.left - arm_w + 6, torso.top + 18, arm_w, 112)
    right_arm = pygame.Rect(torso.right - 6, torso.top + 18, arm_w, 112)
    for arm in (left_arm, right_arm):
        pygame.draw.rect(screen, skin, arm, border_radius=10)
        pygame.draw.rect(screen, (55, 46, 42), arm, width=2, border_radius=10)

    head = character.get("head", "Round")
    if head == "Square":
        head_rect = pygame.Rect(0, 0, 84, 78)
        head_rect.midbottom = (cx, torso.top + 10)
        pygame.draw.rect(screen, skin, head_rect, border_radius=18)
        face_rect = head_rect
    elif head == "Long":
        face_rect = pygame.Rect(0, 0, 74, 96)
        face_rect.midbottom = (cx, torso.top + 10)
        pygame.draw.ellipse(screen, skin, face_rect)
    else:
        face_rect = pygame.Rect(0, 0, 84, 84)
        face_rect.midbottom = (cx, torso.top + 10)
        pygame.draw.ellipse(screen, skin, face_rect)
    pygame.draw.ellipse(screen, (58, 47, 42), face_rect, width=2)

    hair = character.get("hair", "Short")
    if hair != "Bald":
        if hair == "Bob":
            hair_rect = face_rect.inflate(12, 8)
            hair_rect.y -= 10
            pygame.draw.ellipse(screen, hair_col, hair_rect)
            pygame.draw.rect(screen, hair_col, (hair_rect.left, hair_rect.centery, hair_rect.width, hair_rect.height // 2))
        elif hair == "Long":
            hair_rect = face_rect.inflate(18, 36)
            hair_rect.y -= 16
            pygame.draw.ellipse(screen, hair_col, hair_rect)
            pygame.draw.rect(screen, hair_col, (hair_rect.left + 4, hair_rect.centery - 4, hair_rect.width - 8, hair_rect.height // 2), border_radius=12)
        else:
            hair_rect = face_rect.inflate(8, 6)
            hair_rect.y -= 12
            pygame.draw.ellipse(screen, hair_col, hair_rect)
        if head == "Square":
            pygame.draw.rect(screen, skin, face_rect, border_radius=18)
        else:
            pygame.draw.ellipse(screen, skin, face_rect)
        pygame.draw.ellipse(screen, (58, 47, 42), face_rect, width=2)
        pygame.draw.arc(screen, hair_col, face_rect.inflate(8, 4), math.pi, 2 * math.pi, 12)

    eye_y = face_rect.centery - 4
    for ex in (cx - 18, cx + 18):
        pygame.draw.ellipse(screen, (236, 232, 220), (ex - 8, eye_y - 5, 16, 10))
        pygame.draw.circle(screen, eye_col, (ex, eye_y), 4)
        pygame.draw.circle(screen, (16, 16, 16), (ex, eye_y), 2)
    pygame.draw.line(screen, (82, 52, 42), (cx - 10, eye_y + 24), (cx + 10, eye_y + 24), 2)


def draw_character_creator(screen, font_title, font_ui, character, characters,
                           option_buttons, creator_buttons, selected_tab,
                           name_active, screen_w, screen_h):
    screen.fill((17, 20, 25))
    title = font_title.render("Character Creator", True, (232, 214, 154))
    screen.blit(title, (36, 24))

    for key in ("tab_creator", "tab_library"):
        btn = creator_buttons[key]
        btn["color"] = (88, 118, 132) if selected_tab == key else (58, 64, 74)
        draw_button(screen, btn, font_ui)

    draw_button(screen, creator_buttons["back"], font_ui)

    panel = pygame.Rect(32, 132, 365, screen_h - 170)
    pygame.draw.rect(screen, (29, 34, 40), panel, border_radius=8)
    pygame.draw.rect(screen, (84, 91, 100), panel, width=2, border_radius=8)

    if selected_tab == "tab_creator":
        name_rect = creator_buttons["name"]
        pygame.draw.rect(screen, (42, 48, 56), name_rect, border_radius=6)
        pygame.draw.rect(screen, (190, 170, 92) if name_active else (91, 100, 110),
                         name_rect, width=2, border_radius=6)
        name_surf = font_ui.render(character["name"], True, (232, 235, 230))
        screen.blit(name_surf, (name_rect.x + 12, name_rect.y + 9))

        for label, left_btn, right_btn, value_rect, key in option_buttons:
            label_surf = font_ui.render(label, True, (176, 184, 188))
            screen.blit(label_surf, (value_rect.x, value_rect.y - 24))
            pygame.draw.rect(screen, (37, 43, 50), value_rect, border_radius=6)
            value = font_ui.render(character[key], True, (235, 236, 226))
            screen.blit(value, value.get_rect(center=value_rect.center))
            draw_button(screen, left_btn, font_ui)
            draw_button(screen, right_btn, font_ui)

        draw_button(screen, creator_buttons["new"], font_ui)
        draw_button(screen, creator_buttons["save"], font_ui)
    else:
        if not characters:
            empty = font_ui.render("No saved characters yet.", True, (176, 184, 188))
            screen.blit(empty, (panel.x + 18, panel.y + 24))
        for i, saved in enumerate(characters[-12:]):
            row = pygame.Rect(panel.x + 14, panel.y + 18 + i * 44, panel.width - 28, 36)
            pygame.draw.rect(screen, (40, 47, 54), row, border_radius=6)
            name = str(saved.get("name", "Unnamed"))
            diff = str(saved.get("difficulty", "Easy"))
            screen.blit(font_ui.render(name, True, (232, 235, 230)), (row.x + 10, row.y + 8))
            diff_surf = font_ui.render(diff, True, (190, 176, 108))
            screen.blit(diff_surf, diff_surf.get_rect(midright=(row.right - 10, row.centery)))

    preview_rect = pygame.Rect(430, 132, screen_w - 468, screen_h - 170)
    draw_character_preview(screen, character, preview_rect)
    caption = font_ui.render("Layered placeholder art: body, head, eyes, hair, skin tone", True, (154, 164, 168))
    screen.blit(caption, (preview_rect.x + 18, preview_rect.y + 18))


# ---------------------------------------------------------------------------
# Herd / pack overlay visualisation
# ---------------------------------------------------------------------------

def _convex_hull(points):
    pts = list({(int(p[0]), int(p[1])) for p in points})
    n = len(pts)
    if n <= 2:
        return pts
    start = min(pts, key=lambda p: (p[0], p[1]))
    hull = []
    cur = start
    while True:
        hull.append(cur)
        nxt = None
        for cand in pts:
            if cand == cur:
                continue
            if nxt is None:
                nxt = cand
                continue
            cross = ((nxt[0] - cur[0]) * (cand[1] - cur[1]) -
                     (nxt[1] - cur[1]) * (cand[0] - cur[0]))
            if cross > 0 or (cross == 0 and
                    (cand[0]-cur[0])**2 + (cand[1]-cur[1])**2 >
                    (nxt[0]-cur[0])**2 + (nxt[1]-cur[1])**2):
                nxt = cand
        if nxt is None or nxt == start:
            break
        cur = nxt
        if len(hull) > n:
            break
    return hull


def _inflate_hull(hull, cx, cy, pad):
    out = []
    for x, y in hull:
        dx, dy = x - cx, y - cy
        d = math.hypot(dx, dy)
        if d > 0.1:
            out.append((int(x + dx / d * pad), int(y + dy / d * pad)))
        else:
            out.append((int(x + pad), int(y)))
    return out


# Pack color palette — distinct from blue (sheep herds), red (wolf hulls), yellow (home dot)
# Each entry is (R, G, B) for the full-opacity base; alpha is applied per element
_PACK_COLORS = [
    (220,  80, 220),   # 0 violet
    (255, 160,  30),   # 1 orange
    ( 40, 220, 200),   # 2 teal
    (255,  60, 140),   # 3 hot-pink
    (160, 220,  50),   # 4 lime  — use at low alpha so it reads on green grass
    (255, 200,  40),   # 5 amber
    ( 80, 180, 255),   # 6 sky-blue (different enough from herd navy)
    (200, 100,  40),   # 7 burnt-sienna
]
_pack_name_font = None   # lazy-initialised pygame font for pack labels


def draw_group_overlays(screen, overlay_surf, cam_x, cam_y, tile_size,
                        sheep_list, wolf_list, wolf_pack_manager=None):
    sw, sh = screen.get_size()

    if overlay_surf[0] is None or overlay_surf[0].get_size() != (sw, sh):
        overlay_surf[0] = pygame.Surface((sw, sh), pygame.SRCALPHA)
    ov = overlay_surf[0]
    ov.fill((0, 0, 0, 0))

    # ── Sheep herds — blue convex hull ──────────────────────────────────
    herds: dict[int, list] = {}
    for s in sheep_list:
        if not s.alive or getattr(s, 'dead_state', None) is not None:
            continue
        hid = getattr(s, 'herd_id', -1)
        if hid >= 0:
            herds.setdefault(hid, []).append(s)

    for members in herds.values():
        step = max(1, len(members) // 90)
        pts = [(m.tx * tile_size - cam_x, m.ty * tile_size - cam_y)
               for m in members[::step]]
        if not pts:
            continue
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        for px, py in pts:
            pygame.draw.line(ov, (80, 140, 255, 90), (int(cx), int(cy)), (int(px), int(py)), 1)
        ipts = [(int(p[0]), int(p[1])) for p in pts]
        if len(ipts) >= 3:
            hull = _convex_hull(ipts)
            if len(hull) >= 3:
                inflated = _inflate_hull(hull, cx, cy, 14)
                pygame.draw.polygon(ov, (60, 120, 255, 32), inflated)
                pygame.draw.polygon(ov, (100, 160, 255, 130), inflated, 2)
        elif len(ipts) == 2:
            pygame.draw.line(ov, (80, 140, 255, 80), ipts[0], ipts[1], 2)
            for p in ipts:
                pygame.draw.circle(ov, (60, 120, 255, 50), p, 14)
                pygame.draw.circle(ov, (100, 160, 255, 120), p, 14, 2)
        else:
            pygame.draw.circle(ov, (60, 120, 255, 45), (int(cx), int(cy)), 14)
            pygame.draw.circle(ov, (100, 160, 255, 140), (int(cx), int(cy)), 14, 2)
        pygame.draw.circle(ov, (80, 160, 255, 220), (int(cx), int(cy)), 6)
        pygame.draw.circle(ov, (220, 235, 255, 200), (int(cx), int(cy)), 3)

    # ── Wolf pack home-base ranges + member hull ────────────────────────
    global _pack_name_font
    if _pack_name_font is None:
        _pack_name_font = pygame.font.SysFont(None, 16)

    # Build pack_id → color mapping from WolfPackManager
    pack_color_map: dict[int, tuple] = {}
    territories = wolf_pack_manager.get_pack_territories() if wolf_pack_manager else []
    for pack in territories:
        cidx = pack.get("color_idx", 0)
        pack_color_map[pack["pack_id"]] = _PACK_COLORS[cidx % len(_PACK_COLORS)]

    # Draw home-base circles first (behind member hulls)
    for pack in territories:
        hx = pack["home_x"]
        hy = pack["home_y"]
        if hx == 0.0 and hy == 0.0:
            continue
        sx = int(hx * tile_size - cam_x)
        sy = int(hy * tile_size - cam_y)
        hr = max(6, int(pack["home_radius"] * tile_size))
        if sx + hr < 0 or sx - hr > sw or sy + hr < 0 or sy - hr > sh:
            continue

        r, g, b = pack_color_map.get(pack["pack_id"], (220, 80, 220))

        # Filled range circle — pack colour at low alpha
        pygame.draw.circle(ov, (r, g, b, 22), (sx, sy), hr)
        # Outer ring
        pygame.draw.circle(ov, (r, g, b, 180), (sx, sy), hr, 2)
        # Inner ring slightly smaller for depth
        pygame.draw.circle(ov, (r, g, b, 70), (sx, sy), max(3, hr - 6), 1)

        # Home-base centre — always yellow so it pops against any pack colour
        pygame.draw.circle(ov, (255, 240, 60, 240), (sx, sy), 6)
        pygame.draw.circle(ov, (255, 255, 200, 200), (sx, sy), 3)

        # Mode pip: small dot on top of centre — bright = hunting, dim = resting
        if pack["mode"] == "hunt":
            pygame.draw.circle(ov, (255, 80, 40, 240), (sx, sy - 9), 3)
        else:
            pygame.draw.circle(ov, (200, 200, 200, 120), (sx, sy - 9), 3)

        # Pack name label below the centre dot
        name = pack.get("name", "")
        if name and tile_size >= 5:
            txt = _pack_name_font.render(name, True, (r, g, b))
            txt.set_alpha(210)
            rect = txt.get_rect(center=(sx, sy + hr + 10))
            ov.blit(txt, rect)

    # Draw member hull on top of home circles, coloured by pack
    packs: dict[int, list] = {}
    for w in wolf_list:
        if not w.alive or w.dead_state is not None:
            continue
        pid = getattr(w, 'pack_id', -1)
        if pid >= 0:
            packs.setdefault(pid, []).append(w)

    for pid, members in packs.items():
        r, g, b = pack_color_map.get(pid, (220, 80, 220))
        step = max(1, len(members) // 90)
        pts  = [(m.tx * tile_size - cam_x, m.ty * tile_size - cam_y)
                for m in members[::step]]
        if not pts:
            continue
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        for px, py in pts:
            pygame.draw.line(ov, (r, g, b, 70), (int(cx), int(cy)), (int(px), int(py)), 1)
        ipts = [(int(p[0]), int(p[1])) for p in pts]
        if len(ipts) >= 3:
            hull = _convex_hull(ipts)
            if len(hull) >= 3:
                inflated = _inflate_hull(hull, cx, cy, 16)
                pygame.draw.polygon(ov, (r, g, b, 28), inflated)
                pygame.draw.polygon(ov, (r, g, b, 170), inflated, 2)
        elif len(ipts) == 2:
            pygame.draw.line(ov, (r, g, b, 80), ipts[0], ipts[1], 2)
            for p in ipts:
                pygame.draw.circle(ov, (r, g, b, 45), p, 16)
                pygame.draw.circle(ov, (r, g, b, 140), p, 16, 2)
        else:
            pygame.draw.circle(ov, (r, g, b, 50), (int(cx), int(cy)), 16)
            pygame.draw.circle(ov, (r, g, b, 160), (int(cx), int(cy)), 16, 2)
        # Pack centre dot
        pygame.draw.circle(ov, (r, g, b, 220), (int(cx), int(cy)), 5)
        pygame.draw.circle(ov, (255, 255, 255, 180), (int(cx), int(cy)), 2)

    screen.blit(ov, (0, 0))


# ---------------------------------------------------------------------------
# Play UI (bottom bar + popups)
# ---------------------------------------------------------------------------

def draw_play_ui(screen, font_ui, back_btn,
                 spawner_btn, terrain_btn,
                 spawner_mode, terrain_mode,
                 spawner_open, terrain_open,
                 spawner_opt_btns, terrain_opt_btns,
                 seed, tile_size, screen_w, screen_h, is_fullscreen,
                 speed_btns, sim_speed_idx, day_number,
                 stats_btn=None, stats_open=False, stats_lines=None,
                 profiling_btn=None, profiling_open=False, profiling_lines=None,
                 groups_btn=None, show_groups=False,
                 cur_map_w=1024, cur_map_h=1024,
                 flower_opt_btns=None, flower_mode=None):

    bar_rect = pygame.Rect(0, screen_h - BOTTOM_BAR_H, screen_w, BOTTOM_BAR_H)
    pygame.draw.rect(screen, (30, 30, 30), bar_rect)

    # Spawner button — highlight if a spawn mode is active
    spawner_btn["color"] = (55, 155, 75) if spawner_mode is not None else (70, 70, 110)
    draw_button(screen, spawner_btn, font_ui)

    # Terrain button — highlight if a paint mode or flower mode is active
    active_terrain = terrain_mode is not None or flower_mode is not None
    terrain_btn["color"] = (160, 110, 40) if active_terrain else (70, 70, 110)
    draw_button(screen, terrain_btn, font_ui)

    if stats_btn is not None:
        stats_btn["color"] = (85, 125, 165) if stats_open else (70, 70, 110)
        draw_button(screen, stats_btn, font_ui)

    if profiling_btn is not None:
        profiling_btn["color"] = (150, 115, 50) if profiling_open else (70, 70, 110)
        draw_button(screen, profiling_btn, font_ui)

    if groups_btn is not None:
        groups_btn["color"] = (60, 140, 110) if show_groups else (70, 70, 110)
        draw_button(screen, groups_btn, font_ui)

    # --- Spawner popup ---
    if spawner_open:
        _draw_popup_panel(screen, font_ui, spawner_opt_btns, spawner_mode,
                          spawner_btn["rect"].x, screen_h - BOTTOM_BAR_H)

    # --- Terrain popup (terrain types + flower sub-panel stacked above) ---
    if terrain_open:
        terrain_panel_top = _draw_popup_panel(
            screen, font_ui, terrain_opt_btns, terrain_mode,
            terrain_btn["rect"].x, screen_h - BOTTOM_BAR_H)
        # Flower sub-panel sits directly above the terrain panel
        if flower_opt_btns:
            _draw_popup_panel(
                screen, font_ui, flower_opt_btns, flower_mode,
                terrain_btn["rect"].x, terrain_panel_top - 4)

    fs_hint = "F11: windowed" if is_fullscreen else "F11: fullscreen"
    map_label = f"{cur_map_w}x{cur_map_h}"
    hint = font_ui.render(
        f"Day {day_number}   Seed: {seed}   Map: {map_label}   "
        f"Zoom: {round(tile_size)}px   WASD: move   Scroll/+/-: zoom   [: smaller   ]: larger   {fs_hint}",
        True, (160, 160, 160),
    )
    hint_start_x = 16
    for btn in (spawner_btn, terrain_btn, stats_btn, profiling_btn, groups_btn):
        if btn is not None:
            hint_start_x = max(hint_start_x, btn["rect"].right + 16)
    screen.blit(hint, hint.get_rect(midleft=(hint_start_x, screen_h - BOTTOM_BAR_H // 2)))

    draw_button(screen, back_btn, font_ui)

    for i, btn in enumerate(speed_btns):
        if i == sim_speed_idx:
            btn["color"] = (220, 160, 40)
        elif i == 0:
            btn["color"] = (130, 55, 55)
        else:
            btn["color"] = (55, 100, 55)
        draw_button(screen, btn, font_ui)

    if stats_open and stats_lines:
        panel_w = 280
        line_h  = 22
        panel_h = 16 + line_h * len(stats_lines)
        panel_x = screen_w - panel_w - 10
        panel_y = screen_h - BOTTOM_BAR_H - panel_h - 10
        panel   = pygame.Rect(panel_x, panel_y, panel_w, panel_h)
        pygame.draw.rect(screen, (24, 28, 38), panel, border_radius=10)
        pygame.draw.rect(screen, (88, 108, 138), panel, width=2, border_radius=10)
        for i, line in enumerate(stats_lines):
            surf = font_ui.render(line, True, (220, 226, 235))
            screen.blit(surf, (panel_x + 12, panel_y + 10 + i * line_h))

    if profiling_open and profiling_lines:
        panel_w = 360
        line_h = 21
        panel_h = 18 + line_h * len(profiling_lines)
        panel_x = screen_w - panel_w - 10
        stats_offset = 0
        if stats_open and stats_lines:
            stats_offset = 16 + 22 * len(stats_lines) + 12
        panel_y = screen_h - BOTTOM_BAR_H - panel_h - 10 - stats_offset
        panel = pygame.Rect(panel_x, panel_y, panel_w, panel_h)
        pygame.draw.rect(screen, (28, 26, 22), panel, border_radius=10)
        pygame.draw.rect(screen, (150, 122, 70), panel, width=2, border_radius=10)
        for i, line in enumerate(profiling_lines):
            color = (238, 225, 190) if i == 0 else (222, 226, 220)
            surf = font_ui.render(line, True, color)
            screen.blit(surf, (panel_x + 12, panel_y + 10 + i * line_h))


def _draw_popup_panel(screen, font_ui, opt_btns, active_key, anchor_x, panel_bottom):
    """Draw a row of option buttons in a floating panel above the bottom bar.

    Returns the panel's top-y so callers can stack a second panel above it.
    """
    if not opt_btns:
        return panel_bottom
    pad = 8
    btn_h = 34
    # Compute panel width from buttons
    total_w = sum(b["rect"].width for b in opt_btns) + pad * (len(opt_btns) + 1)
    panel_h = btn_h + pad * 2
    panel_x = anchor_x
    panel_y = panel_bottom - panel_h - 6
    panel_rect = pygame.Rect(panel_x, panel_y, total_w, panel_h)

    # Panel background
    pygame.draw.rect(screen, (28, 32, 44), panel_rect, border_radius=10)
    pygame.draw.rect(screen, (80, 100, 130), panel_rect, width=2, border_radius=10)

    # Option buttons
    bx = panel_x + pad
    for btn in opt_btns:
        btn["rect"] = pygame.Rect(bx, panel_y + pad, btn["rect"].width, btn_h)
        # Highlight the active selection
        if btn.get("key") == active_key:
            highlight = tuple(min(255, c + 60) for c in btn["base_color"])
            btn["color"] = highlight
        else:
            btn["color"] = btn["base_color"]
        draw_button(screen, btn, font_ui)
        bx += btn["rect"].width + pad

    return panel_y


# ---------------------------------------------------------------------------
# Camera utilities
# ---------------------------------------------------------------------------

def clamp_camera(cam_x, cam_y, tile_size, screen_w, screen_h,
                 map_w=ISLAND_W, map_h=ISLAND_H):
    map_px_w = map_w * tile_size
    map_px_h = map_h * tile_size
    if map_px_w <= screen_w:
        cam_x = -(screen_w - map_px_w) / 2
    else:
        cam_x = max(0.0, min(cam_x, map_px_w - screen_w))
    if map_px_h <= screen_h:
        cam_y = -(screen_h - map_px_h) / 2
    else:
        cam_y = max(0.0, min(cam_y, map_px_h - screen_h))
    return cam_x, cam_y


def screen_to_world(screen_x, screen_y, cam_x, cam_y, zoom):
    return ((cam_x + screen_x) / zoom, (cam_y + screen_y) / zoom)


def world_to_screen(world_x, world_y, cam_x, cam_y, zoom):
    return (world_x * zoom - cam_x, world_y * zoom - cam_y)


def _brush_bounds(row, col, brush_size):
    """Return the inclusive tile bounds for a square brush at (row, col)."""
    start_row = row - (brush_size - 1) // 2
    start_col = col - (brush_size - 1) // 2
    end_row = start_row + brush_size - 1
    end_col = start_col + brush_size - 1
    return start_row, end_row, start_col, end_col


def _paint_flowers(grid, row, col, ftype, brush, rows, cols, flower_manager):
    """Paint flowers in a square brush centred on (row, col).

    Skips tiles that are not GRASS or already have flowers.
    """
    row0, row1, col0, col1 = _brush_bounds(row, col, brush)
    for nr in range(row0, row1 + 1):
        for nc in range(col0, col1 + 1):
            if (0 <= nr < rows and 0 <= nc < cols
                    and grid[nr][nc] == GRASS
                    and not flower_manager.has_flowers(nc, nr)):
                flower_manager.add(nc, nr, ftype)


def _paint_brush(grid, row, col, terrain_type, brush, rows, cols, notify):
    """Paint a square brush of `brush` x `brush` tiles anchored around (row, col)."""
    row0, row1, col0, col1 = _brush_bounds(row, col, brush)
    for nr in range(row0, row1 + 1):
        for nc in range(col0, col1 + 1):
            if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] != terrain_type:
                grid[nr][nc] = terrain_type
                notify(nr, nc)


def _line_tiles(start_row, start_col, end_row, end_col):
    """Return a 1-tile-thick cardinal line snapped to the dominant drag axis."""
    dr = end_row - start_row
    dc = end_col - start_col
    if abs(dc) >= abs(dr):
        step = 1 if dc >= 0 else -1
        return [(start_row, c) for c in range(start_col, end_col + step, step)]
    step = 1 if dr >= 0 else -1
    return [(r, start_col) for r in range(start_row, end_row + step, step)]


def _paint_line(grid, start_row, start_col, end_row, end_col, terrain_type, notify):
    """Paint a snapped 1-tile-thick cardinal line."""
    for row, col in _line_tiles(start_row, start_col, end_row, end_col):
        if 0 <= row < len(grid) and 0 <= col < len(grid[0]) and grid[row][col] != terrain_type:
            grid[row][col] = terrain_type
            notify(row, col)


# ---------------------------------------------------------------------------
# Button layout  (recalculated every frame so resize is seamless)
# ---------------------------------------------------------------------------

def update_button_layout(
        start_btn, character_creator_btn, options_btn, quit_btn,
        campaign_btn, sandbox_btn, load_btn, start_back_btn,
        island_btn, continent_btn, map_back_btn,
        back_btn, spawner_btn, terrain_btn,
        stats_btn, profiling_btn, groups_btn, speed_btns,
        screen_w, screen_h,
        creator_buttons=None, option_buttons=None):

    # --- Title screen ---
    bw, bh = 260, 58
    bx = screen_w // 2 - bw // 2
    start_y = screen_h // 2 - 20
    start_btn["rect"] = pygame.Rect(bx, start_y, bw, bh)
    character_creator_btn["rect"] = pygame.Rect(bx, start_y + 74, bw, bh)
    options_btn["rect"] = pygame.Rect(bx, start_y + 148, bw, bh)
    quit_btn["rect"] = pygame.Rect(bx, start_y + 222, bw, bh)

    # --- Start game screen ---
    campaign_btn["rect"] = pygame.Rect(bx, screen_h // 2 - 35, bw, bh)
    sandbox_btn["rect"] = pygame.Rect(bx, screen_h // 2 + 39, bw, bh)
    load_btn["rect"] = pygame.Rect(bx, screen_h // 2 + 113, bw, bh)
    start_back_btn["rect"] = pygame.Rect(bx, screen_h // 2 + 187, bw, bh)

    # --- Map select screen ---
    mbw, mbh = 260, 58
    mbx = screen_w // 2 - mbw // 2
    island_btn["rect"]    = pygame.Rect(mbx, screen_h // 2 + 10,  mbw, mbh)
    continent_btn["rect"] = pygame.Rect(mbx, screen_h // 2 + 90,  mbw, mbh)
    map_back_btn["rect"]  = pygame.Rect(mbx, screen_h // 2 + 170, mbw, mbh)

    # --- Play screen ---
    back_btn["rect"] = pygame.Rect(10, 10, 155, 40)

    # Bottom bar: Spawner | Terrain | Stats | Profile | Groups  (left side)
    bbw, bbh = 100, 36
    gap = 6
    by = screen_h - BOTTOM_BAR_H + 6
    spawner_btn["rect"] = pygame.Rect(10,                       by, bbw, bbh)
    terrain_btn["rect"] = pygame.Rect(10 + (bbw + gap),         by, bbw, bbh)
    stats_btn["rect"]   = pygame.Rect(10 + (bbw + gap) * 2,     by, bbw, bbh)
    profiling_btn["rect"] = pygame.Rect(10 + (bbw + gap) * 3,   by, bbw, bbh)
    groups_btn["rect"]  = pygame.Rect(10 + (bbw + gap) * 4,     by, bbw, bbh)

    # Speed buttons — top right, horizontal row
    sbw, sbh = 44, 34
    sgap = 6
    total_w = len(speed_btns) * sbw + (len(speed_btns) - 1) * sgap
    sx = screen_w - total_w - 10
    sy = 10
    for i, btn in enumerate(speed_btns):
        btn["rect"] = pygame.Rect(sx + i * (sbw + sgap), sy, sbw, sbh)

    # --- Character creator ---
    if creator_buttons is not None:
        creator_buttons["tab_creator"]["rect"] = pygame.Rect(36, 92, 120, 34)
        creator_buttons["tab_library"]["rect"] = pygame.Rect(164, 92, 120, 34)
        creator_buttons["back"]["rect"] = pygame.Rect(screen_w - 178, 28, 146, 38)
        creator_buttons["name"]["rect"] = pygame.Rect(52, 158, 315, 40)
        creator_buttons["new"]["rect"] = pygame.Rect(52, screen_h - 82, 130, 38)
        creator_buttons["save"]["rect"] = pygame.Rect(196, screen_h - 82, 170, 38)

    if option_buttons is not None:
        labels = [
            ("Body Type", "body"),
            ("Head Shape", "head"),
            ("Eye Color", "eyes"),
            ("Hair Style", "hair"),
            ("Hair Color", "hair_color"),
            ("Skin Tone", "skin"),
            ("Level Difficulty", "difficulty"),
        ]
        option_buttons.clear()
        y = 230
        for label, key in labels:
            value_rect = pygame.Rect(96, y, 224, 34)
            left_btn = {"label": "<", "rect": pygame.Rect(52, y, 36, 34), "color": (66, 76, 86), "key": key, "dir": -1}
            right_btn = {"label": ">", "rect": pygame.Rect(328, y, 36, 34), "color": (66, 76, 86), "key": key, "dir": 1}
            option_buttons.append((label, left_btn, right_btn, value_rect, key))
            y += 66


def _make_spawner_opt_btns():
    """Create the four NPC spawner option button dicts."""
    btn_w = 110
    btns = []
    for key in (SPAWN_FEMALE_SHEEP, SPAWN_MALE_SHEEP, SPAWN_FEMALE_WOLF, SPAWN_MALE_WOLF):
        col = SPAWNER_COLORS[key]
        btns.append({
            "label":      SPAWNER_LABELS[key],
            "key":        key,
            "base_color": col,
            "color":      col,
            "rect":       pygame.Rect(0, 0, btn_w, 34),
        })
    return btns


def _make_terrain_opt_btns():
    """Create terrain paint option button dicts."""
    labels = {WATER: "Water", SAND: "Sand", DIRT: "Dirt", GRASS: "Grass",
              WALL: "Brick", TUNDRA: "Tundra", SNOW: "Snow"}
    btn_w = 80
    btns = []
    for key in (WATER, SAND, DIRT, GRASS, WALL, TUNDRA, SNOW):
        col = TERRAIN_PAINT_COLORS[key]
        btns.append({
            "label":      labels[key],
            "key":        key,
            "base_color": col,
            "color":      col,
            "rect":       pygame.Rect(0, 0, btn_w, 34),
        })
    return btns


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    pygame.init()
    screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
    pygame.display.set_caption("Sheep Island")
    clock = pygame.time.Clock()
    is_fullscreen = False

    Sheep.load_sprites()
    Ram.load_sprites()
    Wolf.load_sprites()

    font_title = pygame.font.SysFont("Arial", 72, bold=True)
    font_ui    = pygame.font.SysFont("Arial", 20)

    # --- Button dicts ---
    start_btn     = {"label": "Start Game",        "rect": pygame.Rect(0,0,0,0), "color": (55, 130, 55)}
    character_creator_btn = {"label": "Character Creator", "rect": pygame.Rect(0,0,0,0), "color": (65, 105, 135)}
    options_btn   = {"label": "Options",           "rect": pygame.Rect(0,0,0,0), "color": (90, 90, 110)}
    quit_btn      = {"label": "Quit",              "rect": pygame.Rect(0,0,0,0), "color": (150, 55, 55)}

    campaign_btn  = {"label": "Start Campaign",    "rect": pygame.Rect(0,0,0,0), "color": (55, 130, 55)}
    sandbox_btn   = {"label": "Sandbox Sim",       "rect": pygame.Rect(0,0,0,0), "color": (50, 105, 155)}
    load_btn      = {"label": "Load Game",         "rect": pygame.Rect(0,0,0,0), "color": (90, 90, 110)}
    start_back_btn = {"label": "Back to Menu",     "rect": pygame.Rect(0,0,0,0), "color": (80, 80, 80)}

    island_btn    = {"label": "Generate Island",   "rect": pygame.Rect(0,0,0,0), "color": (45, 120, 45)}
    continent_btn = {"label": "Generate Continent","rect": pygame.Rect(0,0,0,0), "color": (40, 90, 160)}
    map_back_btn  = {"label": "Back to Menu",      "rect": pygame.Rect(0,0,0,0), "color": (80, 80, 80)}

    back_btn      = {"label": "Back to Menu",      "rect": pygame.Rect(0,0,0,0), "color": (70, 70, 110)}
    spawner_btn   = {"label": "Spawner",           "rect": pygame.Rect(0,0,0,0), "color": (70, 70, 110)}
    terrain_btn   = {"label": "Terrain",           "rect": pygame.Rect(0,0,0,0), "color": (70, 70, 110)}
    stats_btn     = {"label": "Stats",             "rect": pygame.Rect(0,0,0,0), "color": (70, 70, 110)}
    profiling_btn = {"label": "Profile",           "rect": pygame.Rect(0,0,0,0), "color": (70, 70, 110)}
    groups_btn    = {"label": "Groups",            "rect": pygame.Rect(0,0,0,0), "color": (70, 70, 110)}
    speed_btns    = [{"label": lbl, "rect": pygame.Rect(0,0,0,0), "color": (70, 70, 110)}
                     for lbl in SPEED_LABELS]

    spawner_opt_btns = _make_spawner_opt_btns()
    terrain_opt_btns = _make_terrain_opt_btns()

    # Flower sub-panel buttons (sit above the terrain panel when terrain is open)
    _flower_btn_w = 80
    flower_opt_btns = [
        {"label": "White ✿", "key": Flower.WHITE,
         "base_color": (170, 155, 60), "color": (170, 155, 60),
         "rect": pygame.Rect(0, 0, _flower_btn_w, 34)},
        {"label": "Yellow ✿", "key": Flower.YELLOW,
         "base_color": (170, 145, 30), "color": (170, 145, 30),
         "rect": pygame.Rect(0, 0, _flower_btn_w, 34)},
        {"label": "Red ✿",   "key": Flower.RED,
         "base_color": (160,  55,  55), "color": (160,  55,  55),
         "rect": pygame.Rect(0, 0, _flower_btn_w, 34)},
    ]

    creator_buttons = {
        "tab_creator": {"label": "Creator", "rect": pygame.Rect(0,0,0,0), "color": (58, 64, 74)},
        "tab_library": {"label": "Library", "rect": pygame.Rect(0,0,0,0), "color": (58, 64, 74)},
        "back": {"label": "Back to Menu", "rect": pygame.Rect(0,0,0,0), "color": (70, 70, 110)},
        "name": {"label": "", "rect": pygame.Rect(0,0,0,0), "color": (42, 48, 56)},
        "new": {"label": "New", "rect": pygame.Rect(0,0,0,0), "color": (82, 88, 96)},
        "save": {"label": "Save Character", "rect": pygame.Rect(0,0,0,0), "color": (58, 126, 82)},
    }
    character_option_buttons = []

    title_buttons    = [start_btn, character_creator_btn, options_btn, quit_btn]
    start_game_btns  = [campaign_btn, sandbox_btn, load_btn, start_back_btn]
    map_select_btns  = [island_btn, continent_btn, map_back_btn]

    sim_speed_idx = 1

    # --- Game state ---
    state             = STATE_TITLE
    selected_creator_tab = "tab_creator"
    name_input_active = False
    character_library = load_character_library()
    current_character = make_blank_character()
    _gen_type         = "island"   # "island" or "continent"
    grid              = None
    terrain_renderer  = None
    grass_spread      = None
    tundra_spread     = None
    flower_manager    = FlowerManager()
    flower_mode       = None   # None | Flower.WHITE | Flower.YELLOW | Flower.RED
    current_seed      = random.randint(0, 999_999)
    current_zoom      = TILE_SIZE_DEFAULT
    target_zoom       = TILE_SIZE_DEFAULT
    zoom_anchor_sx    = 640.0
    zoom_anchor_sy    = 360.0
    cam_x, cam_y      = 0.0, 0.0
    cur_map_w         = ISLAND_W
    cur_map_h         = ISLAND_H
    sheep_list: list[Sheep] = []
    wolf_list:  list[Wolf]  = []
    spawner_mode      = None   # None | SPAWN_* constant
    terrain_mode      = None   # None | WATER/SAND/DIRT/GRASS
    terrain_brush     = 1      # square brush side length in tiles
    is_painting       = False  # True while LMB held in terrain mode
    wall_drag_start   = None
    wall_drag_current = None
    spawner_open      = False
    terrain_open      = False
    stats_open        = False
    profiling_open    = False
    show_groups       = False
    regrowth_timers: dict[tuple, float] = {}
    time_of_day       = 0.0
    day_number        = 1
    herd_manager      = HerdManager()
    wolf_pack_manager = WolfPackManager()
    proximity_scanner = ProximityScanner()
    _group_overlay    = [None]

    # Loading screen state
    _gen_thread: threading.Thread | None = None
    _gen_event:  threading.Event  | None = None
    _gen_result: dict                    = {}
    _loading_dot_count  = 0
    _loading_dot_timer  = 0.0
    _lsheep_px   = 0.0
    _lsheep_py   = 0.0
    _lsheep_dx   = 1.0
    _lsheep_dy   = 0.0
    _lsheep_speed = 55.0
    _lsheep_timer = 0.0
    _lsheep_grazing = False
    _lsheep_facing  = "right"
    _lsheep_surf: pygame.Surface | None = None
    _profile_ms: dict[str, float] = {}
    _profile_counts: dict[str, int] = {}
    _frame_ms = 0.0
    _sim_frame = 0
    _herd_update_accum = 0.0
    _terrain_spread_accum = 0.0

    def _profile_add(label: str, start_time: float):
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        prev = _profile_ms.get(label)
        _profile_ms[label] = elapsed_ms if prev is None else prev * 0.9 + elapsed_ms * 0.1

    def _profile_count(label: str, count: int):
        _profile_counts[label] = count

    def _profile_lines():
        lines = [
            f"Frame avg: {_frame_ms:5.1f} ms  FPS est: {1000.0 / max(_frame_ms, 0.001):4.0f}",
            f"Sheep: {len(sheep_list)}   Wolves: {len(wolf_list)}",
        ]
        for label, ms in sorted(_profile_ms.items(), key=lambda item: item[1], reverse=True)[:10]:
            count = _profile_counts.get(label)
            suffix = f" ({count})" if count is not None else ""
            lines.append(f"{label}: {ms:5.2f} ms{suffix}")
        return lines

    def _sheep_update_stride(count: int) -> int:
        if count >= 450:
            return 4
        if count >= 300:
            return 3
        if count >= 160:
            return 2
        return 1

    def mark_terrain_changed(row: int, col: int):
        if terrain_renderer is not None:
            terrain_renderer.mark_dirty(row, col)
        if grass_spread is not None:
            grass_spread.on_tile_changed(row, col)
        if tundra_spread is not None:
            tundra_spread.on_tile_changed(row, col)
        # Remove flowers from tiles that are no longer grass
        if grid is not None and grid[row][col] != GRASS:
            flower_manager.remove_tile(col, row)

    def _start_generation(gen_type: str, seed: int):
        nonlocal _gen_thread, _gen_event, _gen_result
        nonlocal _lsheep_px, _lsheep_py, _lsheep_dx, _lsheep_dy
        nonlocal _lsheep_timer, _lsheep_grazing, _lsheep_facing, _lsheep_surf
        nonlocal _loading_dot_count, _loading_dot_timer

        _gen_event  = threading.Event()
        _gen_result = {}
        _seed_local = seed
        _type_local = gen_type

        def _do_generate(seed=_seed_local, result=_gen_result,
                         ev=_gen_event, gt=_type_local):
            if gt == "continent":
                gen = ContinentGenerator(seed=seed)
            else:
                gen = MapGenerator(ISLAND_W, ISLAND_H, seed=seed)
            g = gen.generate()
            flood_fill_grass(g)
            result["grid"] = g
            ev.set()

        _gen_thread = threading.Thread(target=_do_generate, daemon=True)
        _gen_thread.start()

        screen_w, screen_h = screen.get_size()
        _lsheep_px   = screen_w / 2.0
        _lsheep_py   = screen_h / 2.0 + 120
        angle0       = random.uniform(0, 2 * math.pi)
        _lsheep_dx   = math.cos(angle0)
        _lsheep_dy   = math.sin(angle0)
        _lsheep_timer   = random.uniform(1.5, 3.0)
        _lsheep_grazing = False
        _loading_dot_count = 0
        _loading_dot_timer = 0.0
        _lsheep_surf = Sheep._scaled("right", 18.0)

    # ---------------------------------------------------------------------------
    # Game loop
    # ---------------------------------------------------------------------------
    while True:
        dt                  = clock.tick(60) / 1000.0
        screen_w, screen_h  = screen.get_size()
        mouse_pos           = pygame.mouse.get_pos()

        update_button_layout(
            start_btn, character_creator_btn, options_btn, quit_btn,
            campaign_btn, sandbox_btn, load_btn, start_back_btn,
            island_btn, continent_btn, map_back_btn,
            back_btn, spawner_btn, terrain_btn,
            stats_btn, profiling_btn, groups_btn, speed_btns,
            screen_w, screen_h,
            creator_buttons=creator_buttons,
            option_buttons=character_option_buttons)

        # --- Events ---
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_F11:
                    if is_fullscreen:
                        screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
                        is_fullscreen = False
                    else:
                        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
                        is_fullscreen = True
                    screen_w, screen_h = screen.get_size()
                    if grid is not None:
                        cam_x, cam_y = clamp_camera(cam_x, cam_y, current_zoom,
                                                    screen_w, screen_h,
                                                    cur_map_w, cur_map_h)

                if state == STATE_PLAY:
                    if event.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                        target_zoom = min(TILE_SIZE_MAX, target_zoom * ZOOM_FACTOR)
                        zoom_anchor_sx, zoom_anchor_sy = screen_w / 2.0, screen_h / 2.0

                    elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                        target_zoom = max(TILE_SIZE_MIN, target_zoom / ZOOM_FACTOR)
                        zoom_anchor_sx, zoom_anchor_sy = screen_w / 2.0, screen_h / 2.0

                    elif event.key == pygame.K_RIGHTBRACKET and terrain_mode is not None:
                        terrain_brush = min(20, terrain_brush + 1)

                    elif event.key == pygame.K_LEFTBRACKET and terrain_mode is not None:
                        terrain_brush = max(1, terrain_brush - 1)

                    elif event.key == pygame.K_ESCAPE:
                        state        = STATE_TITLE
                        sheep_list   = []
                        wolf_list    = []
                        spawner_mode = None
                        terrain_mode = None
                        is_painting  = False
                        wall_drag_start = None
                        wall_drag_current = None
                        spawner_open = False
                        terrain_open = False
                        stats_open   = False
                        profiling_open = False
                        show_groups  = False

                elif state == STATE_CHARACTER_CREATOR:
                    if event.key == pygame.K_ESCAPE:
                        name_input_active = False
                        state = STATE_TITLE
                    elif name_input_active:
                        if event.key == pygame.K_BACKSPACE:
                            current_character["name"] = current_character["name"][:-1]
                        elif event.key == pygame.K_RETURN:
                            name_input_active = False
                        elif event.unicode and len(current_character["name"]) < 24:
                            if event.unicode.isprintable():
                                current_character["name"] += event.unicode

            if event.type == pygame.MOUSEWHEEL and state == STATE_PLAY:
                if event.y > 0:
                    target_zoom = min(TILE_SIZE_MAX, target_zoom * ZOOM_FACTOR)
                else:
                    target_zoom = max(TILE_SIZE_MIN, target_zoom / ZOOM_FACTOR)
                zoom_anchor_sx, zoom_anchor_sy = float(mouse_pos[0]), float(mouse_pos[1])

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:

                # ---- Title screen ----
                if state == STATE_TITLE:
                    if start_btn["rect"].collidepoint(mouse_pos):
                        state = STATE_START_GAME

                    elif character_creator_btn["rect"].collidepoint(mouse_pos):
                        selected_creator_tab = "tab_creator"
                        state = STATE_CHARACTER_CREATOR

                    elif options_btn["rect"].collidepoint(mouse_pos):
                        pass

                    elif quit_btn["rect"].collidepoint(mouse_pos):
                        pygame.quit()
                        sys.exit()

                # ---- Start game screen ----
                elif state == STATE_START_GAME:
                    if campaign_btn["rect"].collidepoint(mouse_pos):
                        pass

                    elif sandbox_btn["rect"].collidepoint(mouse_pos):
                        state = STATE_MAP_SELECT

                    elif load_btn["rect"].collidepoint(mouse_pos):
                        pass

                    elif start_back_btn["rect"].collidepoint(mouse_pos):
                        state = STATE_TITLE

                # ---- Map select screen ----
                elif state == STATE_MAP_SELECT:
                    if island_btn["rect"].collidepoint(mouse_pos):
                        _gen_type    = "island"
                        current_seed = random.randint(0, 999_999)
                        _start_generation("island", current_seed)
                        state = STATE_LOADING

                    elif continent_btn["rect"].collidepoint(mouse_pos):
                        _gen_type    = "continent"
                        current_seed = random.randint(0, 999_999)
                        _start_generation("continent", current_seed)
                        state = STATE_LOADING

                    elif map_back_btn["rect"].collidepoint(mouse_pos):
                        state = STATE_START_GAME

                # ---- Character creator screen ----
                elif state == STATE_CHARACTER_CREATOR:
                    if creator_buttons["back"]["rect"].collidepoint(mouse_pos):
                        name_input_active = False
                        state = STATE_TITLE
                    elif creator_buttons["tab_creator"]["rect"].collidepoint(mouse_pos):
                        selected_creator_tab = "tab_creator"
                    elif creator_buttons["tab_library"]["rect"].collidepoint(mouse_pos):
                        selected_creator_tab = "tab_library"
                    elif selected_creator_tab == "tab_creator":
                        if creator_buttons["name"]["rect"].collidepoint(mouse_pos):
                            name_input_active = True
                        elif creator_buttons["new"]["rect"].collidepoint(mouse_pos):
                            current_character = make_blank_character()
                            name_input_active = False
                        elif creator_buttons["save"]["rect"].collidepoint(mouse_pos):
                            saved = dict(current_character)
                            if not saved["name"].strip():
                                saved["name"] = f"Human {len(character_library) + 1}"
                            character_library.append(saved)
                            save_character_library(character_library)
                            selected_creator_tab = "tab_library"
                            name_input_active = False
                        else:
                            name_input_active = False
                            for _label, left_btn, right_btn, _value_rect, key in character_option_buttons:
                                if left_btn["rect"].collidepoint(mouse_pos):
                                    cycle_character_option(current_character, key, -1)
                                    break
                                if right_btn["rect"].collidepoint(mouse_pos):
                                    cycle_character_option(current_character, key, 1)
                                    break

                # ---- Play screen ----
                elif state == STATE_PLAY:
                    clicked_ui = False

                    if back_btn["rect"].collidepoint(mouse_pos):
                        state        = STATE_TITLE
                        sheep_list   = []
                        wolf_list    = []
                        spawner_mode = None
                        terrain_mode = None
                        is_painting  = False
                        wall_drag_start = None
                        wall_drag_current = None
                        spawner_open = False
                        terrain_open = False
                        stats_open   = False
                        profiling_open = False
                        show_groups  = False
                        clicked_ui   = True

                    elif spawner_btn["rect"].collidepoint(mouse_pos):
                        if spawner_mode is not None:
                            # Deactivate current spawner tool
                            spawner_mode = None
                            spawner_open = False
                        else:
                            spawner_open = not spawner_open
                            terrain_open = False
                            terrain_mode = None
                        clicked_ui = True

                    elif terrain_btn["rect"].collidepoint(mouse_pos):
                        if terrain_mode is not None:
                            terrain_mode = None
                            terrain_open = False
                        else:
                            terrain_open = not terrain_open
                            spawner_open = False
                            spawner_mode = None
                        clicked_ui = True

                    elif stats_btn["rect"].collidepoint(mouse_pos):
                        stats_open = not stats_open
                        clicked_ui = True

                    elif profiling_btn["rect"].collidepoint(mouse_pos):
                        profiling_open = not profiling_open
                        clicked_ui = True

                    elif groups_btn["rect"].collidepoint(mouse_pos):
                        show_groups = not show_groups
                        clicked_ui = True

                    else:
                        for i, btn in enumerate(speed_btns):
                            if btn["rect"].collidepoint(mouse_pos):
                                sim_speed_idx = i
                                clicked_ui = True
                                break

                    # Spawner popup option buttons
                    if spawner_open and not clicked_ui:
                        for opt in spawner_opt_btns:
                            if opt["rect"].collidepoint(mouse_pos):
                                if spawner_mode == opt["key"]:
                                    spawner_mode = None
                                else:
                                    spawner_mode = opt["key"]
                                spawner_open = False
                                clicked_ui = True
                                break

                    # Terrain popup option buttons
                    if terrain_open and not clicked_ui:
                        for opt in terrain_opt_btns:
                            if opt["rect"].collidepoint(mouse_pos):
                                if terrain_mode == opt["key"]:
                                    terrain_mode = None
                                else:
                                    terrain_mode = opt["key"]
                                    flower_mode  = None
                                terrain_open = False
                                clicked_ui = True
                                break

                    # Flower sub-panel buttons (shown when terrain panel is open)
                    if terrain_open and not clicked_ui:
                        for opt in flower_opt_btns:
                            if opt["rect"].collidepoint(mouse_pos):
                                if flower_mode == opt["key"]:
                                    flower_mode = None
                                else:
                                    flower_mode  = opt["key"]
                                    terrain_mode = None
                                terrain_open = False
                                clicked_ui = True
                                break

                    # Map click — place NPC or paint terrain
                    if not clicked_ui and mouse_pos[1] < screen_h - BOTTOM_BAR_H:
                        tx, ty  = screen_to_world(mouse_pos[0], mouse_pos[1],
                                                  cam_x, cam_y, current_zoom)
                        col     = int(tx)
                        row     = int(ty)
                        rows    = len(grid)
                        cols    = len(grid[0]) if rows else 0
                        in_bounds = 0 <= row < rows and 0 <= col < cols
                        on_land   = in_bounds and is_walkable_terrain(grid[row][col])

                        if spawner_mode is not None and on_land:
                            if spawner_mode == SPAWN_FEMALE_SHEEP:
                                sheep_list.append(Sheep(tx, ty))
                            elif spawner_mode == SPAWN_MALE_SHEEP:
                                sheep_list.append(Ram(tx, ty))
                            elif spawner_mode == SPAWN_FEMALE_WOLF:
                                wolf_list.append(Wolf(tx, ty, sex="female"))
                            elif spawner_mode == SPAWN_MALE_WOLF:
                                wolf_list.append(Wolf(tx, ty, sex="male"))

                        elif terrain_mode is not None and in_bounds:
                            if terrain_mode == WALL:
                                if grid[row][col] != WATER:
                                    wall_drag_start = (row, col)
                                    wall_drag_current = (row, col)
                            else:
                                _paint_brush(grid, row, col, terrain_mode,
                                             terrain_brush, rows, cols,
                                             mark_terrain_changed)
                                is_painting = True

                        elif flower_mode is not None and in_bounds:
                            _paint_flowers(grid, row, col, flower_mode,
                                           terrain_brush, rows, cols, flower_manager)
                            is_painting = True

            if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                if (state == STATE_PLAY and terrain_mode == WALL
                        and wall_drag_start is not None and wall_drag_current is not None):
                    _paint_line(grid,
                                wall_drag_start[0], wall_drag_start[1],
                                wall_drag_current[0], wall_drag_current[1],
                                WALL, mark_terrain_changed)
                    wall_drag_start = None
                    wall_drag_current = None
                is_painting = False

            if event.type == pygame.MOUSEMOTION and is_painting and state == STATE_PLAY:
                if mouse_pos[1] < screen_h - BOTTOM_BAR_H:
                    tx, ty    = screen_to_world(mouse_pos[0], mouse_pos[1],
                                                cam_x, cam_y, current_zoom)
                    col       = int(tx)
                    row       = int(ty)
                    rows      = len(grid)
                    cols      = len(grid[0]) if rows else 0
                    in_bounds = 0 <= row < rows and 0 <= col < cols
                    if in_bounds and terrain_mode is not None:
                        _paint_brush(grid, row, col, terrain_mode,
                                     terrain_brush, rows, cols,
                                     mark_terrain_changed)
                    elif in_bounds and flower_mode is not None:
                        _paint_flowers(grid, row, col, flower_mode,
                                       terrain_brush, rows, cols, flower_manager)
            elif (event.type == pygame.MOUSEMOTION and state == STATE_PLAY
                  and terrain_mode == WALL and wall_drag_start is not None):
                if mouse_pos[1] < screen_h - BOTTOM_BAR_H:
                    tx, ty = screen_to_world(mouse_pos[0], mouse_pos[1], cam_x, cam_y, current_zoom)
                    col = int(tx)
                    row = int(ty)
                    rows = len(grid)
                    cols = len(grid[0]) if rows else 0
                    if 0 <= row < rows and 0 <= col < cols and grid[row][col] != WATER:
                        wall_drag_current = (row, col)

        # --- Loading state ---
        if state == STATE_LOADING:
            _loading_dot_timer += dt
            if _loading_dot_timer >= 0.45:
                _loading_dot_timer = 0.0
                _loading_dot_count = (_loading_dot_count + 1) % 3

            _lsheep_timer -= dt
            if _lsheep_grazing:
                if _lsheep_timer <= 0:
                    _lsheep_grazing = False
                    angle_l = random.uniform(0, 2 * math.pi)
                    _lsheep_dx = math.cos(angle_l)
                    _lsheep_dy = math.sin(angle_l)
                    _lsheep_timer = random.uniform(1.8, 3.5)
                    _lsheep_facing = "right" if _lsheep_dx >= 0 else "left"
                    _lsheep_surf = Sheep._scaled(f"eat_{_lsheep_facing}", 18.0)
            else:
                if _lsheep_timer <= 0:
                    if random.random() < 0.35:
                        _lsheep_grazing = True
                        _lsheep_timer   = random.uniform(1.2, 2.5)
                        _lsheep_surf = Sheep._scaled(f"eat_{_lsheep_facing}", 18.0)
                    else:
                        angle_l = random.uniform(0, 2 * math.pi)
                        _lsheep_dx = math.cos(angle_l)
                        _lsheep_dy = math.sin(angle_l)
                        _lsheep_timer = random.uniform(1.5, 3.0)
                        _lsheep_facing = "right" if _lsheep_dx >= 0 else "left"
                        _lsheep_surf = Sheep._scaled(_lsheep_facing, 18.0)
                else:
                    _lsheep_px += _lsheep_dx * _lsheep_speed * dt
                    _lsheep_py += _lsheep_dy * _lsheep_speed * dt
                    margin = 80
                    if _lsheep_px < margin:
                        _lsheep_px = margin
                        _lsheep_dx = abs(_lsheep_dx)
                        _lsheep_facing = "right"
                        _lsheep_surf = Sheep._scaled("right", 18.0)
                    elif _lsheep_px > screen_w - margin:
                        _lsheep_px = screen_w - margin
                        _lsheep_dx = -abs(_lsheep_dx)
                        _lsheep_facing = "left"
                        _lsheep_surf = Sheep._scaled("left", 18.0)
                    if _lsheep_py < margin:
                        _lsheep_py = margin
                        _lsheep_dy = abs(_lsheep_dy)
                    elif _lsheep_py > screen_h - margin:
                        _lsheep_py = screen_h - margin
                        _lsheep_dy = -abs(_lsheep_dy)

            if _gen_event is not None and _gen_event.is_set():
                if _gen_thread is not None:
                    _gen_thread.join()
                    _gen_thread = None
                grid             = _gen_result["grid"]
                cur_map_h        = len(grid)
                cur_map_w        = len(grid[0]) if cur_map_h else ISLAND_W
                terrain_renderer = TerrainRenderer(grid)
                grass_spread     = GrassSpread(grid)
                tundra_spread    = TundraSpread(grid)
                default_ts       = CONTINENT_TILE_DEFAULT if _gen_type == "continent" else TILE_SIZE_DEFAULT
                current_zoom     = float(default_ts)
                target_zoom      = float(default_ts)
                cam_x            = max(0.0, (cur_map_w * current_zoom - screen_w) / 2)
                cam_y            = max(0.0, (cur_map_h * current_zoom - screen_h) / 2)
                sheep_list       = []
                wolf_list        = []
                spawner_mode     = None
                terrain_mode     = None
                flower_mode      = None
                flower_manager   = FlowerManager()
                wall_drag_start  = None
                wall_drag_current = None
                spawner_open     = False
                terrain_open     = False
                stats_open       = False
                profiling_open   = False
                show_groups      = False
                regrowth_timers  = {}
                time_of_day      = 0.0
                day_number       = 1
                herd_manager     = HerdManager()
                wolf_pack_manager = WolfPackManager()
                proximity_scanner = ProximityScanner()
                _sim_frame       = 0
                _herd_update_accum = 0.0
                _terrain_spread_accum = 0.0
                _gen_event       = None
                state            = STATE_PLAY


        # --- Camera movement ---
        if state == STATE_PLAY:
            play_update_start = time.perf_counter()
            target_zoom = max(TILE_SIZE_MIN, min(TILE_SIZE_MAX, target_zoom))
            if abs(current_zoom - target_zoom) > 0.001:
                anchor_tx, anchor_ty = screen_to_world(
                    zoom_anchor_sx, zoom_anchor_sy, cam_x, cam_y, current_zoom
                )
                current_zoom += (target_zoom - current_zoom) * min(1.0, 12.0 * dt)
                current_zoom = max(TILE_SIZE_MIN, min(TILE_SIZE_MAX, current_zoom))
                cam_x = anchor_tx * current_zoom - zoom_anchor_sx
                cam_y = anchor_ty * current_zoom - zoom_anchor_sy
                cam_x, cam_y = clamp_camera(cam_x, cam_y, current_zoom,
                                            screen_w, screen_h,
                                            cur_map_w, cur_map_h)
            else:
                current_zoom = target_zoom

            speed = CAMERA_SPEED * (TILE_SIZE_DEFAULT / max(current_zoom, TILE_SIZE_MIN)) ** 0.15
            keys  = pygame.key.get_pressed()
            if keys[pygame.K_a]:
                cam_x -= speed
            if keys[pygame.K_d]:
                cam_x += speed
            if keys[pygame.K_w]:
                cam_y -= speed
            if keys[pygame.K_s]:
                cam_y += speed
            cam_x, cam_y = clamp_camera(cam_x, cam_y, current_zoom,
                                        screen_w, screen_h,
                                        cur_map_w, cur_map_h)

            dt_sim = dt * SPEED_SCALES[sim_speed_idx]
            _sim_frame += 1

            prev_time_of_day = time_of_day
            time_of_day = (time_of_day + dt_sim) % DAY_CYCLE_DURATION
            if time_of_day < prev_time_of_day:
                day_number += 1

            scanner_start = time.perf_counter()
            proximity_scanner.update(sheep_list, wolf_list)
            _profile_add("proximity scanner", scanner_start)

            herd_start = time.perf_counter()
            _herd_update_accum += dt_sim
            if (_herd_update_accum >= HERD_UPDATE_STEP or len(sheep_list) < 120
                    or sim_speed_idx == 0):
                herd_dt = _herd_update_accum
                _herd_update_accum = 0.0
                herd_manager.update(herd_dt, sheep_list, grid, wolves=wolf_list)
            _profile_add("herd update", herd_start)

            ram_start = time.perf_counter()
            Ram.update_fights(dt_sim)
            _profile_add("ram fights", ram_start)

            pack_start = time.perf_counter()
            wolf_pack_manager.update(dt_sim, wolf_list, sheep_list, grid)
            _profile_add("wolf pack update", pack_start)

            wolves_start = time.perf_counter()
            new_wolves: list[Wolf] = []
            for wolf in wolf_list:
                wolf.update(dt_sim, grid, sheep_list, wolf_list, new_wolves)
            wolf_list = [w for w in wolf_list if w.alive]
            wolf_list.extend(new_wolves)
            _profile_add("wolf entity update", wolves_start)
            _profile_count("wolf entity update", len(wolf_list))

            sheep_start = time.perf_counter()
            new_sheep: list[Sheep] = []
            sheep_count_before = len(sheep_list)
            sheep_stride = _sheep_update_stride(sheep_count_before)
            sheep_batch = _sim_frame % sheep_stride
            sheep_dt = dt_sim * sheep_stride
            for idx, sheep in enumerate(sheep_list):
                if sheep_stride > 1 and idx % sheep_stride != sheep_batch:
                    continue
                sheep.update(sheep_dt, grid, regrowth_timers, sheep_list, new_sheep,
                             dirty_callback=mark_terrain_changed)
            sheep_list = [s for s in sheep_list if s.alive]
            sheep_list.extend(new_sheep)
            _profile_add("sheep entity update", sheep_start)
            _profile_count("sheep entity update", len(sheep_list))
            _profile_count("sheep update batch", max(1, sheep_count_before // sheep_stride))

            regrowth_start = time.perf_counter()
            for pos in list(regrowth_timers):
                regrowth_timers[pos] -= dt_sim
                if regrowth_timers[pos] <= 0:
                    r, c = pos
                    if 0 <= r < len(grid) and 0 <= c < len(grid[0]):
                        grid[r][c] = GRASS
                        mark_terrain_changed(r, c)
                    del regrowth_timers[pos]
            _profile_add("grass regrowth", regrowth_start)
            _profile_count("grass regrowth", len(regrowth_timers))

            spread_start = time.perf_counter()
            _terrain_spread_accum += dt_sim
            if (_terrain_spread_accum >= TERRAIN_SPREAD_STEP or len(sheep_list) < 120
                    or sim_speed_idx == 0):
                spread_dt = _terrain_spread_accum
                _terrain_spread_accum = 0.0
                if grass_spread is not None:
                    grass_spread.update(spread_dt, notify=mark_terrain_changed)
                if tundra_spread is not None:
                    tundra_spread.update(spread_dt, notify=mark_terrain_changed)
            _profile_add("terrain spread", spread_start)

            terrain_anim_start = time.perf_counter()
            terrain_renderer.update(dt_sim)
            _profile_add("terrain animation", terrain_anim_start)
            _profile_add("play update total", play_update_start)

        # --- Render ---
        if state == STATE_TITLE:
            draw_title(screen, font_title, font_ui, title_buttons, screen_w, screen_h)

        elif state == STATE_START_GAME:
            draw_start_game(screen, font_title, font_ui, start_game_btns,
                            screen_w, screen_h)

        elif state == STATE_MAP_SELECT:
            draw_map_select(screen, font_title, font_ui, map_select_btns,
                            screen_w, screen_h)

        elif state == STATE_CHARACTER_CREATOR:
            draw_character_creator(
                screen, font_title, font_ui, current_character, character_library,
                character_option_buttons, creator_buttons, selected_creator_tab,
                name_input_active, screen_w, screen_h,
            )

        elif state == STATE_LOADING:
            draw_loading(screen, font_title, font_ui,
                         _loading_dot_count, _lsheep_surf,
                         _lsheep_px, _lsheep_py, screen_w, screen_h,
                         gen_type=_gen_type)

        elif state == STATE_PLAY:
            screen.fill(WATER_COLOR)
            terrain_draw_start = time.perf_counter()
            terrain_renderer.draw(screen, current_zoom, cam_x, cam_y, screen_w, screen_h)
            _profile_add("terrain draw", terrain_draw_start)

            flower_draw_start = time.perf_counter()
            flower_manager.draw_all(screen, cam_x, cam_y, current_zoom, screen_w, screen_h)
            _profile_add("flower draw", flower_draw_start)

            sheep_draw_start = time.perf_counter()
            for sheep in sheep_list:
                sheep.draw(screen, cam_x, cam_y, current_zoom)
            _profile_add("sheep draw", sheep_draw_start)
            _profile_count("sheep draw", len(sheep_list))

            wolf_draw_start = time.perf_counter()
            for wolf in wolf_list:
                wolf.draw(screen, cam_x, cam_y, current_zoom)
            _profile_add("wolf draw", wolf_draw_start)
            _profile_count("wolf draw", len(wolf_list))

            if show_groups:
                group_draw_start = time.perf_counter()
                draw_group_overlays(screen, _group_overlay, cam_x, cam_y, current_zoom,
                                    sheep_list, wolf_list, wolf_pack_manager)
                _profile_add("group overlay draw", group_draw_start)

            # Day/night overlay
            overlay_start = time.perf_counter()
            cycle_pos   = time_of_day / DAY_CYCLE_DURATION
            night_factor = 0.5 - 0.5 * math.cos(2 * math.pi * cycle_pos)
            if night_factor > 0.01:
                alpha = int(night_factor * 155)
                night_surf = pygame.Surface((screen_w, screen_h))
                night_surf.fill((8, 18, 55))
                night_surf.set_alpha(alpha)
                screen.blit(night_surf, (0, 0))
            _profile_add("day night overlay", overlay_start)

            ui_start = time.perf_counter()
            living        = [s for s in sheep_list if getattr(s, 'dead_state', None) is None]
            living_count  = len(living)
            ram_count     = sum(1 for s in living if isinstance(s, Ram))
            living_wolves = [w for w in wolf_list if w.alive and w.dead_state is None]
            wolf_living   = len(living_wolves)
            wolf_female_count = sum(1 for w in living_wolves if getattr(w, "sex", "") == "female")
            wolf_male_count   = sum(1 for w in living_wolves if getattr(w, "sex", "") == "male")
            herd_count = len(getattr(herd_manager, "_herds", {}))
            pack_count = len(getattr(wolf_pack_manager, "_packs", {}))
            stats_lines = [
                f"Herds: {herd_count}",
                f"Packs: {pack_count}",
                f"Female sheep: {living_count - ram_count}",
                f"Male sheep (rams): {ram_count}",
                f"Female wolves: {wolf_female_count}",
                f"Male wolves: {wolf_male_count}",
            ]
            draw_play_ui(
                screen, font_ui, back_btn,
                spawner_btn, terrain_btn,
                spawner_mode, terrain_mode,
                spawner_open, terrain_open,
                spawner_opt_btns, terrain_opt_btns,
                current_seed, current_zoom, screen_w, screen_h, is_fullscreen,
                speed_btns, sim_speed_idx, day_number,
                stats_btn=stats_btn, stats_open=stats_open, stats_lines=stats_lines,
                profiling_btn=profiling_btn, profiling_open=profiling_open,
                profiling_lines=_profile_lines(),
                groups_btn=groups_btn, show_groups=show_groups,
                cur_map_w=cur_map_w, cur_map_h=cur_map_h,
                flower_opt_btns=flower_opt_btns, flower_mode=flower_mode,
            )

            # Crosshair cursor when a tool is active
            if mouse_pos[1] < screen_h - BOTTOM_BAR_H:
                if spawner_mode is not None:
                    color = SPAWNER_COLORS[spawner_mode]
                    mx, my = mouse_pos
                    pygame.draw.line(screen, color, (mx - 10, my), (mx + 10, my), 2)
                    pygame.draw.line(screen, color, (mx, my - 10), (mx, my + 10), 2)
                    pygame.draw.circle(screen, color, (mx, my), 6, 1)
                elif terrain_mode is not None:
                    color = TERRAIN_PAINT_COLORS[terrain_mode]
                    mx, my = mouse_pos
                    tx, ty = screen_to_world(mx, my, cam_x, cam_y, current_zoom)
                    col = int(tx)
                    row = int(ty)
                    if terrain_mode == WALL:
                        preview_start = wall_drag_start if wall_drag_start is not None else (row, col)
                        preview_end = wall_drag_current if wall_drag_current is not None else (row, col)
                        for prow, pcol in _line_tiles(preview_start[0], preview_start[1],
                                                      preview_end[0], preview_end[1]):
                            hx0, hy0 = world_to_screen(pcol, prow, cam_x, cam_y, current_zoom)
                            hx1, hy1 = world_to_screen(pcol + 1, prow + 1, cam_x, cam_y, current_zoom)
                            rect = (round(hx0), round(hy0),
                                    max(1, round(hx1 - hx0)), max(1, round(hy1 - hy0)))
                            pygame.draw.rect(screen, color, rect, 2)
                            fill = pygame.Surface((rect[2], rect[3]), pygame.SRCALPHA)
                            fill.fill((*color, 55))
                            screen.blit(fill, (rect[0], rect[1]))
                    else:
                        row0, row1, col0, col1 = _brush_bounds(row, col, terrain_brush)
                        hx0, hy0 = world_to_screen(col0, row0, cam_x, cam_y, current_zoom)
                        hx1, hy1 = world_to_screen(col1 + 1, row1 + 1, cam_x, cam_y, current_zoom)
                        pygame.draw.rect(
                            screen,
                            color,
                            (round(hx0), round(hy0), max(1, round(hx1 - hx0)), max(1, round(hy1 - hy0))),
                            2,
                        )
                        if terrain_brush > 1:
                            brush_label = font_ui.render(f"{terrain_brush}x{terrain_brush}", True, color)
                            screen.blit(brush_label, (mx + 8, my - 16))
            _profile_add("play ui draw", ui_start)

        flip_start = time.perf_counter()
        pygame.display.flip()
        _profile_add("display flip", flip_start)
        frame_elapsed = dt * 1000.0
        _frame_ms = frame_elapsed if _frame_ms <= 0 else _frame_ms * 0.9 + frame_elapsed * 0.1


if __name__ == "__main__":
    main()
