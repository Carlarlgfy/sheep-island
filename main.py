import pygame
import sys
import random
import math
import threading

from mapgen import MapGenerator, ContinentGenerator, flood_fill_grass, WATER, SAND, DIRT, GRASS
from sheep import Sheep
from ram import Ram
from grass import TerrainRenderer, GrassSpread, WATER_COLOR
from herd import HerdManager
from wolf import Wolf
from wolf_pack import WolfPackManager

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
CAMERA_SPEED                = 55

STATE_TITLE      = "title"
STATE_MAP_SELECT = "map_select"
STATE_LOADING    = "loading"
STATE_PLAY       = "play"

DAY_CYCLE_DURATION = 300.0

BOTTOM_BAR_H = 48

SPEED_SCALES = [0, 1, 3, 8]
SPEED_LABELS = ["||", ">", ">>", ">>>"]

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
    WATER: ( 38, 100, 182),
    SAND:  (170, 150,  80),
    DIRT:  (100,  70,  30),
    GRASS: ( 40, 100,  25),
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
    """Map type selection screen shown after clicking 'Generate Map'."""
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


def draw_group_overlays(screen, overlay_surf, cam_x, cam_y, tile_size,
                        sheep_list, wolf_list):
    sw, sh = screen.get_size()

    if overlay_surf[0] is None or overlay_surf[0].get_size() != (sw, sh):
        overlay_surf[0] = pygame.Surface((sw, sh), pygame.SRCALPHA)
    ov = overlay_surf[0]
    ov.fill((0, 0, 0, 0))

    herds: dict[int, list] = {}
    for s in sheep_list:
        if not s.alive or getattr(s, 'dead_state', None) is not None:
            continue
        hid = getattr(s, 'herd_id', -1)
        if hid >= 0:
            herds.setdefault(hid, []).append(s)

    for members in herds.values():
        pts = [(m.tx * tile_size - cam_x, m.ty * tile_size - cam_y) for m in members]
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

    packs: dict[int, list] = {}
    for w in wolf_list:
        if not w.alive or w.dead_state is not None:
            continue
        pid = getattr(w, 'pack_id', -1)
        if pid >= 0:
            packs.setdefault(pid, []).append(w)

    for members in packs.values():
        pts = [(m.tx * tile_size - cam_x, m.ty * tile_size - cam_y) for m in members]
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)

        for px, py in pts:
            pygame.draw.line(ov, (255, 80, 60, 90), (int(cx), int(cy)), (int(px), int(py)), 1)

        ipts = [(int(p[0]), int(p[1])) for p in pts]
        if len(ipts) >= 3:
            hull = _convex_hull(ipts)
            if len(hull) >= 3:
                inflated = _inflate_hull(hull, cx, cy, 16)
                pygame.draw.polygon(ov, (255, 60, 40, 36), inflated)
                pygame.draw.polygon(ov, (255, 110, 80, 140), inflated, 2)
        elif len(ipts) == 2:
            pygame.draw.line(ov, (255, 80, 60, 80), ipts[0], ipts[1], 2)
            for p in ipts:
                pygame.draw.circle(ov, (255, 60, 40, 50), p, 16)
                pygame.draw.circle(ov, (255, 110, 80, 130), p, 16, 2)
        else:
            pygame.draw.circle(ov, (255, 60, 40, 50), (int(cx), int(cy)), 16)
            pygame.draw.circle(ov, (255, 110, 80, 140), (int(cx), int(cy)), 16, 2)

        pygame.draw.circle(ov, (255, 100, 70, 220), (int(cx), int(cy)), 6)
        pygame.draw.circle(ov, (255, 230, 220, 200), (int(cx), int(cy)), 3)

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
                 groups_btn=None, show_groups=False,
                 cur_map_w=1024, cur_map_h=1024):

    bar_rect = pygame.Rect(0, screen_h - BOTTOM_BAR_H, screen_w, BOTTOM_BAR_H)
    pygame.draw.rect(screen, (30, 30, 30), bar_rect)

    # Spawner button — highlight if a spawn mode is active
    spawner_btn["color"] = (55, 155, 75) if spawner_mode is not None else (70, 70, 110)
    draw_button(screen, spawner_btn, font_ui)

    # Terrain button — highlight if a paint mode is active
    terrain_btn["color"] = (160, 110, 40) if terrain_mode is not None else (70, 70, 110)
    draw_button(screen, terrain_btn, font_ui)

    if stats_btn is not None:
        stats_btn["color"] = (85, 125, 165) if stats_open else (70, 70, 110)
        draw_button(screen, stats_btn, font_ui)

    if groups_btn is not None:
        groups_btn["color"] = (60, 140, 110) if show_groups else (70, 70, 110)
        draw_button(screen, groups_btn, font_ui)

    # --- Spawner popup ---
    if spawner_open:
        _draw_popup_panel(screen, font_ui, spawner_opt_btns, spawner_mode,
                          spawner_btn["rect"].x, screen_h - BOTTOM_BAR_H)

    # --- Terrain popup ---
    if terrain_open:
        _draw_popup_panel(screen, font_ui, terrain_opt_btns, terrain_mode,
                          terrain_btn["rect"].x, screen_h - BOTTOM_BAR_H)

    fs_hint = "F11: windowed" if is_fullscreen else "F11: fullscreen"
    map_label = f"{cur_map_w}x{cur_map_h}"
    hint = font_ui.render(
        f"Day {day_number}   Seed: {seed}   Map: {map_label}   "
        f"Zoom: {round(tile_size)}px   WASD: move   Scroll/+/-: zoom   {fs_hint}",
        True, (160, 160, 160),
    )
    hint_start_x = 16
    for btn in (spawner_btn, terrain_btn, stats_btn, groups_btn):
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


def _draw_popup_panel(screen, font_ui, opt_btns, active_key, anchor_x, panel_bottom):
    """Draw a row of option buttons in a floating panel above the bottom bar."""
    if not opt_btns:
        return
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


def _paint_brush(grid, row, col, terrain_type, brush, rows, cols, notify):
    """Paint a square brush of radius `brush` tiles centered on (row, col)."""
    r = brush - 1
    for dr in range(-r, r + 1):
        for dc in range(-r, r + 1):
            nr, nc = row + dr, col + dc
            if 0 <= nr < rows and 0 <= nc < cols:
                if grid[nr][nc] != terrain_type:
                    grid[nr][nc] = terrain_type
                    notify(nr, nc)


# ---------------------------------------------------------------------------
# Button layout  (recalculated every frame so resize is seamless)
# ---------------------------------------------------------------------------

def update_button_layout(
        gen_btn, quit_btn,
        island_btn, continent_btn, map_back_btn,
        back_btn, spawner_btn, terrain_btn,
        stats_btn, groups_btn, speed_btns,
        screen_w, screen_h):

    # --- Title screen ---
    bw, bh = 260, 58
    bx = screen_w // 2 - bw // 2
    gen_btn["rect"]  = pygame.Rect(bx, screen_h // 2 + 20,  bw, bh)
    quit_btn["rect"] = pygame.Rect(bx, screen_h // 2 + 100, bw, bh)

    # --- Map select screen ---
    mbw, mbh = 260, 58
    mbx = screen_w // 2 - mbw // 2
    island_btn["rect"]    = pygame.Rect(mbx, screen_h // 2 + 10,  mbw, mbh)
    continent_btn["rect"] = pygame.Rect(mbx, screen_h // 2 + 90,  mbw, mbh)
    map_back_btn["rect"]  = pygame.Rect(mbx, screen_h // 2 + 170, mbw, mbh)

    # --- Play screen ---
    back_btn["rect"] = pygame.Rect(10, 10, 155, 40)

    # Bottom bar: Spawner | Terrain | Stats | Groups  (left side)
    bbw, bbh = 100, 36
    gap = 6
    by = screen_h - BOTTOM_BAR_H + 6
    spawner_btn["rect"] = pygame.Rect(10,                       by, bbw, bbh)
    terrain_btn["rect"] = pygame.Rect(10 + (bbw + gap),         by, bbw, bbh)
    stats_btn["rect"]   = pygame.Rect(10 + (bbw + gap) * 2,     by, bbw, bbh)
    groups_btn["rect"]  = pygame.Rect(10 + (bbw + gap) * 3,     by, bbw, bbh)

    # Speed buttons — top right, horizontal row
    sbw, sbh = 44, 34
    sgap = 6
    total_w = len(speed_btns) * sbw + (len(speed_btns) - 1) * sgap
    sx = screen_w - total_w - 10
    sy = 10
    for i, btn in enumerate(speed_btns):
        btn["rect"] = pygame.Rect(sx + i * (sbw + sgap), sy, sbw, sbh)


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
    """Create the four terrain paint option button dicts."""
    labels = {WATER: "Water", SAND: "Sand", DIRT: "Dirt", GRASS: "Grass"}
    btn_w = 80
    btns = []
    for key in (WATER, SAND, DIRT, GRASS):
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
    gen_btn       = {"label": "Generate Map",      "rect": pygame.Rect(0,0,0,0), "color": (55, 130, 55)}
    quit_btn      = {"label": "Quit",              "rect": pygame.Rect(0,0,0,0), "color": (150, 55, 55)}

    island_btn    = {"label": "Generate Island",   "rect": pygame.Rect(0,0,0,0), "color": (45, 120, 45)}
    continent_btn = {"label": "Generate Continent","rect": pygame.Rect(0,0,0,0), "color": (40, 90, 160)}
    map_back_btn  = {"label": "Back to Menu",      "rect": pygame.Rect(0,0,0,0), "color": (80, 80, 80)}

    back_btn      = {"label": "Back to Menu",      "rect": pygame.Rect(0,0,0,0), "color": (70, 70, 110)}
    spawner_btn   = {"label": "Spawner",           "rect": pygame.Rect(0,0,0,0), "color": (70, 70, 110)}
    terrain_btn   = {"label": "Terrain",           "rect": pygame.Rect(0,0,0,0), "color": (70, 70, 110)}
    stats_btn     = {"label": "Stats",             "rect": pygame.Rect(0,0,0,0), "color": (70, 70, 110)}
    groups_btn    = {"label": "Groups",            "rect": pygame.Rect(0,0,0,0), "color": (70, 70, 110)}
    speed_btns    = [{"label": lbl, "rect": pygame.Rect(0,0,0,0), "color": (70, 70, 110)}
                     for lbl in SPEED_LABELS]

    spawner_opt_btns = _make_spawner_opt_btns()
    terrain_opt_btns = _make_terrain_opt_btns()

    title_buttons    = [gen_btn, quit_btn]
    map_select_btns  = [island_btn, continent_btn, map_back_btn]

    sim_speed_idx = 1

    # --- Game state ---
    state             = STATE_TITLE
    _gen_type         = "island"   # "island" or "continent"
    grid              = None
    terrain_renderer  = None
    grass_spread      = None
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
    terrain_brush     = 1      # brush radius in tiles (1 = single tile)
    is_painting       = False  # True while LMB held in terrain mode
    spawner_open      = False
    terrain_open      = False
    stats_open        = False
    show_groups       = False
    regrowth_timers: dict[tuple, float] = {}
    time_of_day       = 0.0
    day_number        = 1
    herd_manager      = HerdManager()
    wolf_pack_manager = WolfPackManager()
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

    def mark_terrain_changed(row: int, col: int):
        if terrain_renderer is not None:
            terrain_renderer.mark_dirty(row, col)
        if grass_spread is not None:
            grass_spread.on_tile_changed(row, col)

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
            gen_btn, quit_btn,
            island_btn, continent_btn, map_back_btn,
            back_btn, spawner_btn, terrain_btn,
            stats_btn, groups_btn, speed_btns,
            screen_w, screen_h)

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
                        terrain_brush = min(8, terrain_brush + 1)

                    elif event.key == pygame.K_LEFTBRACKET and terrain_mode is not None:
                        terrain_brush = max(1, terrain_brush - 1)

                    elif event.key == pygame.K_ESCAPE:
                        state        = STATE_TITLE
                        sheep_list   = []
                        wolf_list    = []
                        spawner_mode = None
                        terrain_mode = None
                        is_painting  = False
                        spawner_open = False
                        terrain_open = False
                        stats_open   = False
                        show_groups  = False

            if event.type == pygame.MOUSEWHEEL and state == STATE_PLAY:
                if event.y > 0:
                    target_zoom = min(TILE_SIZE_MAX, target_zoom * ZOOM_FACTOR)
                else:
                    target_zoom = max(TILE_SIZE_MIN, target_zoom / ZOOM_FACTOR)
                zoom_anchor_sx, zoom_anchor_sy = float(mouse_pos[0]), float(mouse_pos[1])

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:

                # ---- Title screen ----
                if state == STATE_TITLE:
                    if gen_btn["rect"].collidepoint(mouse_pos):
                        state = STATE_MAP_SELECT

                    elif quit_btn["rect"].collidepoint(mouse_pos):
                        pygame.quit()
                        sys.exit()

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
                        state = STATE_TITLE

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
                        spawner_open = False
                        terrain_open = False
                        stats_open   = False
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
                        on_land   = in_bounds and grid[row][col] != WATER

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
                            _paint_brush(grid, row, col, terrain_mode,
                                         terrain_brush, rows, cols,
                                         mark_terrain_changed)
                            is_painting = True

            if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                is_painting = False

            if event.type == pygame.MOUSEMOTION and is_painting and state == STATE_PLAY:
                if terrain_mode is not None and mouse_pos[1] < screen_h - BOTTOM_BAR_H:
                    tx, ty  = screen_to_world(mouse_pos[0], mouse_pos[1],
                                              cam_x, cam_y, current_zoom)
                    col     = int(tx)
                    row     = int(ty)
                    rows    = len(grid)
                    cols    = len(grid[0]) if rows else 0
                    in_bounds = 0 <= row < rows and 0 <= col < cols
                    if in_bounds:
                        _paint_brush(grid, row, col, terrain_mode,
                                     terrain_brush, rows, cols,
                                     mark_terrain_changed)

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
                default_ts       = CONTINENT_TILE_DEFAULT if _gen_type == "continent" else TILE_SIZE_DEFAULT
                current_zoom     = float(default_ts)
                target_zoom      = float(default_ts)
                cam_x            = max(0.0, (cur_map_w * current_zoom - screen_w) / 2)
                cam_y            = max(0.0, (cur_map_h * current_zoom - screen_h) / 2)
                sheep_list       = []
                wolf_list        = []
                spawner_mode     = None
                terrain_mode     = None
                spawner_open     = False
                terrain_open     = False
                stats_open       = False
                show_groups      = False
                regrowth_timers  = {}
                time_of_day      = 0.0
                day_number       = 1
                herd_manager     = HerdManager()
                wolf_pack_manager = WolfPackManager()
                _gen_event       = None
                state            = STATE_PLAY


        # --- Camera movement ---
        if state == STATE_PLAY:
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

            speed = max(1.0, CAMERA_SPEED * current_zoom / TILE_SIZE_DEFAULT)
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

            prev_time_of_day = time_of_day
            time_of_day = (time_of_day + dt_sim) % DAY_CYCLE_DURATION
            if time_of_day < prev_time_of_day:
                day_number += 1

            herd_manager.update(dt_sim, sheep_list, grid, wolves=wolf_list)
            Ram.update_fights(dt_sim)

            wolf_pack_manager.update(dt_sim, wolf_list, sheep_list)
            new_wolves: list[Wolf] = []
            for wolf in wolf_list:
                wolf.update(dt_sim, grid, sheep_list, wolf_list, new_wolves)
            wolf_list = [w for w in wolf_list if w.alive]
            wolf_list.extend(new_wolves)

            new_sheep: list[Sheep] = []
            for sheep in sheep_list:
                sheep.update(dt_sim, grid, regrowth_timers, sheep_list, new_sheep,
                             dirty_callback=mark_terrain_changed)
            sheep_list = [s for s in sheep_list if s.alive]
            sheep_list.extend(new_sheep)

            for pos in list(regrowth_timers):
                regrowth_timers[pos] -= dt_sim
                if regrowth_timers[pos] <= 0:
                    r, c = pos
                    if 0 <= r < len(grid) and 0 <= c < len(grid[0]):
                        grid[r][c] = GRASS
                        mark_terrain_changed(r, c)
                    del regrowth_timers[pos]

            if grass_spread is not None:
                grass_spread.update(dt_sim, notify=mark_terrain_changed)

            terrain_renderer.update(dt_sim)

        # --- Render ---
        if state == STATE_TITLE:
            draw_title(screen, font_title, font_ui, title_buttons, screen_w, screen_h)

        elif state == STATE_MAP_SELECT:
            draw_map_select(screen, font_title, font_ui, map_select_btns,
                            screen_w, screen_h)

        elif state == STATE_LOADING:
            draw_loading(screen, font_title, font_ui,
                         _loading_dot_count, _lsheep_surf,
                         _lsheep_px, _lsheep_py, screen_w, screen_h,
                         gen_type=_gen_type)

        elif state == STATE_PLAY:
            screen.fill(WATER_COLOR)
            terrain_renderer.draw(screen, current_zoom, cam_x, cam_y, screen_w, screen_h)
            for sheep in sheep_list:
                sheep.draw(screen, cam_x, cam_y, current_zoom)
            for wolf in wolf_list:
                wolf.draw(screen, cam_x, cam_y, current_zoom)

            if show_groups:
                draw_group_overlays(screen, _group_overlay, cam_x, cam_y, current_zoom,
                                    sheep_list, wolf_list)

            # Day/night overlay
            cycle_pos   = time_of_day / DAY_CYCLE_DURATION
            night_factor = 0.5 - 0.5 * math.cos(2 * math.pi * cycle_pos)
            if night_factor > 0.01:
                alpha = int(night_factor * 155)
                night_surf = pygame.Surface((screen_w, screen_h))
                night_surf.fill((8, 18, 55))
                night_surf.set_alpha(alpha)
                screen.blit(night_surf, (0, 0))

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
                groups_btn=groups_btn, show_groups=show_groups,
                cur_map_w=cur_map_w, cur_map_h=cur_map_h,
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
                    r = terrain_brush - 1
                    hx0, hy0 = world_to_screen(col - r, row - r, cam_x, cam_y, current_zoom)
                    hx1, hy1 = world_to_screen(col + r + 1, row + r + 1, cam_x, cam_y, current_zoom)
                    pygame.draw.rect(
                        screen,
                        color,
                        (round(hx0), round(hy0), max(1, round(hx1 - hx0)), max(1, round(hy1 - hy0))),
                        2,
                    )
                    if terrain_brush > 1:
                        brush_label = font_ui.render(f"{terrain_brush * 2 - 1}x{terrain_brush * 2 - 1}", True, color)
                        screen.blit(brush_label, (mx + 8, my - 16))

        pygame.display.flip()


if __name__ == "__main__":
    main()
