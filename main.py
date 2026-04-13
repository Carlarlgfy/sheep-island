import pygame
import sys
import random
import math
import threading

from mapgen import MapGenerator, WATER, SAND, DIRT, GRASS
from sheep import Sheep
from ram import Ram
from grass import TerrainRenderer, GrassSpread, WATER_COLOR
from herd import HerdManager
from wolf import Wolf
from wolf_pack import WolfPackManager

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAP_W, MAP_H = 1024, 1024

TILE_SIZE_DEFAULT = 14.0
TILE_SIZE_MIN     = 2.0
TILE_SIZE_MAX     = 48.0
ZOOM_FACTOR       = 1.1   # multiplicative zoom per step
ZOOM_LERP         = 18.0  # smooth zoom animation speed (higher = snappier)
CAMERA_SPEED      = 55


STATE_TITLE   = "title"
STATE_LOADING = "loading"
STATE_PLAY    = "play"

DAY_CYCLE_DURATION = 300.0   # seconds for a full day/night cycle

BOTTOM_BAR_H = 48

SPEED_SCALES  = [0, 1, 3, 8]    # sim dt multipliers: paused, 1x, 3x, 8x
SPEED_LABELS  = ["||", ">", ">>", ">>>"]


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



def draw_loading(screen, font_title, font_ui, dot_count, sheep_surf,
                 sheep_px, sheep_py, screen_w, screen_h):
    screen.fill((18, 18, 36))
    dots = "." * (dot_count + 1)   # cycles 1–3 dots
    label = font_title.render(f"Loading{dots}", True, (240, 215, 90))
    screen.blit(label, label.get_rect(center=(screen_w // 2, screen_h // 2 - 40)))
    hint = font_ui.render("generating island…", True, (100, 100, 140))
    screen.blit(hint, hint.get_rect(center=(screen_w // 2, screen_h // 2 + 30)))
    if sheep_surf is not None:
        w, h = sheep_surf.get_size()
        screen.blit(sheep_surf, (int(sheep_px) - w // 2, int(sheep_py) - h // 2))


def draw_play_ui(screen, font_ui, back_btn, sheep_btn, wolf_btn,
                 sheep_tool, wolf_tool,
                 seed, tile_size, screen_w, screen_h, is_fullscreen, population,
                 speed_btns, sim_speed_idx, day_number, ram_count=0, wolf_count=0,
                 wolf_female_count=0, wolf_male_count=0,
                 stats_btn=None, stats_open=False, stats_lines=None):
    bar_rect = pygame.Rect(0, screen_h - BOTTOM_BAR_H, screen_w, BOTTOM_BAR_H)
    pygame.draw.rect(screen, (30, 30, 30), bar_rect)

    sheep_btn["color"] = (60, 140, 60) if sheep_tool else (70, 70, 110)
    wolf_btn["color"]  = (160, 60, 60) if wolf_tool  else (70, 70, 110)
    draw_button(screen, sheep_btn, font_ui)
    draw_button(screen, wolf_btn,  font_ui)

    ewe_count = population - ram_count
    fs_hint = "F11: windowed" if is_fullscreen else "F11: fullscreen"
    hint = font_ui.render(
        f"Day {day_number}   Seed: {seed}   Zoom: {round(tile_size)}px   "
        f"Sheep: female {ewe_count} male {ram_count}   Wolves: female {wolf_female_count} male {wolf_male_count} total {wolf_count}   "
        f"WASD: move   Scroll/+/-: zoom   {fs_hint}",
        True, (160, 160, 160),
    )
    screen.blit(hint, hint.get_rect(midleft=(wolf_btn["rect"].right + 16,
                                              screen_h - BOTTOM_BAR_H // 2)))
    draw_button(screen, back_btn, font_ui)

    # Speed buttons — highlight the active one
    for i, btn in enumerate(speed_btns):
        if i == sim_speed_idx:
            btn["color"] = (220, 160, 40)   # gold = active
        elif i == 0:
            btn["color"] = (130, 55, 55)    # red tint for pause
        else:
            btn["color"] = (55, 100, 55)    # green tint for play speeds
        draw_button(screen, btn, font_ui)

    if stats_btn is not None:
        stats_btn["color"] = (85, 125, 165) if stats_open else (70, 70, 110)
        draw_button(screen, stats_btn, font_ui)

    if stats_open and stats_lines:
        panel_w = 280
        line_h = 22
        panel_h = 16 + line_h * len(stats_lines)
        panel_x = screen_w - panel_w - 10
        panel_y = screen_h - BOTTOM_BAR_H - panel_h - 10
        panel = pygame.Rect(panel_x, panel_y, panel_w, panel_h)
        pygame.draw.rect(screen, (24, 28, 38), panel, border_radius=10)
        pygame.draw.rect(screen, (88, 108, 138), panel, width=2, border_radius=10)
        for i, line in enumerate(stats_lines):
            surf = font_ui.render(line, True, (220, 226, 235))
            screen.blit(surf, (panel_x + 12, panel_y + 10 + i * line_h))


# ---------------------------------------------------------------------------
# Camera utilities
# ---------------------------------------------------------------------------

def clamp_camera(cam_x, cam_y, tile_size, screen_w, screen_h):
    map_px_w = MAP_W * tile_size
    map_px_h = MAP_H * tile_size
    # When map fits inside the screen, center it (negative cam offset).
    # Otherwise clamp to the valid scroll range so the map fills the screen edge.
    if map_px_w <= screen_w:
        cam_x = -(screen_w - map_px_w) / 2
    else:
        cam_x = max(0.0, min(cam_x, map_px_w - screen_w))
    if map_px_h <= screen_h:
        cam_y = -(screen_h - map_px_h) / 2
    else:
        cam_y = max(0.0, min(cam_y, map_px_h - screen_h))
    return cam_x, cam_y


def zoom_camera(cam_x, cam_y, old_size, new_size, screen_w, screen_h,
                anchor_sx=None, anchor_sy=None):
    """Keep the chosen screen anchor fixed while zooming."""
    scale    = new_size / old_size
    if anchor_sx is None:
        anchor_sx = screen_w // 2
    if anchor_sy is None:
        anchor_sy = screen_h // 2
    world_x  = cam_x + anchor_sx
    world_y  = cam_y + anchor_sy
    cam_x    = world_x * scale - anchor_sx
    cam_y    = world_y * scale - anchor_sy
    return clamp_camera(cam_x, cam_y, new_size, screen_w, screen_h)


# ---------------------------------------------------------------------------
# Button layout  (recalculated every frame so resize is seamless)
# ---------------------------------------------------------------------------

def update_button_layout(gen_btn, quit_btn, back_btn, sheep_btn, wolf_btn, stats_btn,
                         speed_btns, screen_w, screen_h):
    bw, bh = 260, 58
    bx = screen_w // 2 - bw // 2
    gen_btn["rect"]   = pygame.Rect(bx, screen_h // 2 + 20,  bw, bh)
    quit_btn["rect"]  = pygame.Rect(bx, screen_h // 2 + 100, bw, bh)
    back_btn["rect"]  = pygame.Rect(10, 10, 155, 40)
    sheep_btn["rect"] = pygame.Rect(10,  screen_h - BOTTOM_BAR_H + 6, 100, 36)
    wolf_btn["rect"]  = pygame.Rect(116, screen_h - BOTTOM_BAR_H + 6, 100, 36)
    stats_btn["rect"] = pygame.Rect(screen_w - 100, screen_h - BOTTOM_BAR_H + 6, 90, 36)

    # Speed buttons — top right, horizontal row
    sbw, sbh = 44, 34
    gap = 6
    total_w = len(speed_btns) * sbw + (len(speed_btns) - 1) * gap
    sx = screen_w - total_w - 10
    sy = 10
    for i, btn in enumerate(speed_btns):
        btn["rect"] = pygame.Rect(sx + i * (sbw + gap), sy, sbw, sbh)


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

    gen_btn   = {"label": "Generate Map", "rect": pygame.Rect(0, 0, 0, 0), "color": (55, 130, 55)}
    quit_btn  = {"label": "Quit",         "rect": pygame.Rect(0, 0, 0, 0), "color": (150, 55, 55)}
    back_btn  = {"label": "Back to Menu", "rect": pygame.Rect(0, 0, 0, 0), "color": (70, 70, 110)}
    sheep_btn = {"label": "Sheep",        "rect": pygame.Rect(0, 0, 0, 0), "color": (70, 70, 110)}
    wolf_btn  = {"label": "Wolf",         "rect": pygame.Rect(0, 0, 0, 0), "color": (70, 70, 110)}
    stats_btn = {"label": "Stats",        "rect": pygame.Rect(0, 0, 0, 0), "color": (70, 70, 110)}
    speed_btns = [{"label": lbl, "rect": pygame.Rect(0, 0, 0, 0), "color": (70, 70, 110)}
                  for lbl in SPEED_LABELS]
    title_buttons = [gen_btn, quit_btn]
    sim_speed_idx = 1   # default: normal speed

    state            = STATE_TITLE
    grid             = None
    terrain_renderer = None
    grass_spread     = None
    current_seed     = random.randint(0, 999_999)
    tile_size        = TILE_SIZE_DEFAULT
    target_tile_size = TILE_SIZE_DEFAULT
    cam_x, cam_y     = 0.0, 0.0
    sheep_list: list[Sheep] = []
    wolf_list:  list[Wolf]  = []
    sheep_tool       = False
    wolf_tool        = False
    stats_open       = False
    regrowth_timers: dict[tuple, float] = {}   # (row, col) → seconds until grass returns
    time_of_day      = 0.0   # seconds into current day cycle
    day_number       = 1
    herd_manager     = HerdManager()
    wolf_pack_manager = WolfPackManager()

    # Loading screen state
    _gen_thread: threading.Thread | None = None
    _gen_event:  threading.Event  | None = None
    _gen_result: dict                    = {}
    _loading_dot_count  = 0     # 0-2 cycling → 1-3 dots
    _loading_dot_timer  = 0.0
    # Simple loading sheep: position in pixels, direction, state timer
    _lsheep_px   = 0.0
    _lsheep_py   = 0.0
    _lsheep_dx   = 1.0
    _lsheep_dy   = 0.0
    _lsheep_speed = 55.0        # pixels per second
    _lsheep_timer = 0.0         # >0 = walking, ≤0 = grazing pause
    _lsheep_grazing = False
    _lsheep_facing  = "right"
    _lsheep_surf: pygame.Surface | None = None
    zoom_anchor_x   = 0
    zoom_anchor_y   = 0

    def mark_terrain_changed(row: int, col: int):
        if terrain_renderer is not None:
            terrain_renderer.mark_dirty(row, col)
        if grass_spread is not None:
            grass_spread.on_tile_changed(row, col)

    # ---------------------------------------------------------------------------
    # Game loop
    # ---------------------------------------------------------------------------
    while True:
        dt                   = clock.tick(60) / 1000.0
        screen_w, screen_h   = screen.get_size()
        mouse_pos            = pygame.mouse.get_pos()

        update_button_layout(gen_btn, quit_btn, back_btn, sheep_btn, wolf_btn, stats_btn,
                             speed_btns, screen_w, screen_h)

        # --- Events ---
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

            if event.type == pygame.KEYDOWN:
                # Fullscreen toggle — works from any state
                if event.key == pygame.K_F11:
                    if is_fullscreen:
                        screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
                        is_fullscreen = False
                    else:
                        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
                        is_fullscreen = True
                    screen_w, screen_h = screen.get_size()
                    if grid is not None:
                        cam_x, cam_y = clamp_camera(cam_x, cam_y, tile_size, screen_w, screen_h)

                if state == STATE_PLAY:
                    if event.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                        zoom_anchor_x, zoom_anchor_y = screen_w // 2, screen_h // 2
                        target_tile_size = min(TILE_SIZE_MAX, target_tile_size * ZOOM_FACTOR)

                    elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                        zoom_anchor_x, zoom_anchor_y = screen_w // 2, screen_h // 2
                        target_tile_size = max(TILE_SIZE_MIN, target_tile_size / ZOOM_FACTOR)

                    elif event.key == pygame.K_ESCAPE:
                        state      = STATE_TITLE
                        sheep_list = []
                        wolf_list  = []
                        sheep_tool = False
                        wolf_tool  = False
                        stats_open = False

            if event.type == pygame.MOUSEWHEEL and state == STATE_PLAY:
                zoom_anchor_x, zoom_anchor_y = mouse_pos
                if event.y > 0:
                    target_tile_size = min(TILE_SIZE_MAX, target_tile_size * ZOOM_FACTOR)
                else:
                    target_tile_size = max(TILE_SIZE_MIN, target_tile_size / ZOOM_FACTOR)

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if state == STATE_TITLE:
                    if gen_btn["rect"].collidepoint(mouse_pos):
                        current_seed = random.randint(0, 999_999)
                        _seed_for_thread = current_seed
                        _gen_event  = threading.Event()
                        _gen_result = {}

                        def _do_generate(seed=_seed_for_thread, result=_gen_result,
                                         ev=_gen_event):
                            gen = MapGenerator(MAP_W, MAP_H, seed=seed)
                            result['grid'] = gen.generate()
                            ev.set()

                        _gen_thread = threading.Thread(target=_do_generate, daemon=True)
                        _gen_thread.start()

                        # Set up loading sheep centered on screen
                        _lsheep_px   = screen_w / 2.0
                        _lsheep_py   = screen_h / 2.0 + 120
                        angle0       = random.uniform(0, 2 * math.pi)
                        _lsheep_dx   = math.cos(angle0)
                        _lsheep_dy   = math.sin(angle0)
                        _lsheep_timer   = random.uniform(1.5, 3.0)
                        _lsheep_grazing = False
                        _loading_dot_count = 0
                        _loading_dot_timer = 0.0
                        # Pre-scale loading sheep sprite
                        _lsheep_surf = Sheep._scaled("right", 18.0)
                        state = STATE_LOADING

                    elif quit_btn["rect"].collidepoint(mouse_pos):
                        pygame.quit()
                        sys.exit()

                elif state == STATE_PLAY:
                    if back_btn["rect"].collidepoint(mouse_pos):
                        state      = STATE_TITLE
                        sheep_list = []
                        wolf_list  = []
                        sheep_tool = False
                        wolf_tool  = False
                        stats_open = False

                    elif sheep_btn["rect"].collidepoint(mouse_pos):
                        sheep_tool = not sheep_tool
                        if sheep_tool:
                            wolf_tool = False

                    elif wolf_btn["rect"].collidepoint(mouse_pos):
                        wolf_tool = not wolf_tool
                        if wolf_tool:
                            sheep_tool = False

                    elif stats_btn["rect"].collidepoint(mouse_pos):
                        stats_open = not stats_open

                    else:
                        for i, btn in enumerate(speed_btns):
                            if btn["rect"].collidepoint(mouse_pos):
                                sim_speed_idx = i
                                break

                    if state == STATE_PLAY and mouse_pos[1] < screen_h - BOTTOM_BAR_H:
                        world_x = mouse_pos[0] + cam_x
                        world_y = mouse_pos[1] + cam_y
                        ts      = max(1, round(tile_size))
                        col     = int(world_x // ts)
                        row     = int(world_y // ts)
                        rows    = len(grid)
                        cols    = len(grid[0]) if rows else 0
                        on_land = (0 <= row < rows and 0 <= col < cols
                                   and grid[row][col] != WATER)

                        if sheep_tool and on_land:
                            if random.random() < 0.25:
                                sheep_list.append(Ram(world_x / ts, world_y / ts))
                            else:
                                sheep_list.append(Sheep(world_x / ts, world_y / ts))

                        elif wolf_tool and on_land:
                            sex = "male" if random.random() < 0.5 else "female"
                            wolf_list.append(Wolf(world_x / ts, world_y / ts, sex=sex))

        # --- Loading state: animate dot text + wandering sheep; check thread ---
        if state == STATE_LOADING:
            _loading_dot_timer += dt
            if _loading_dot_timer >= 0.45:
                _loading_dot_timer = 0.0
                _loading_dot_count = (_loading_dot_count + 1) % 3

            # Update loading sheep
            _lsheep_timer -= dt
            if _lsheep_grazing:
                # Occasionally cycle eating sprite
                if _lsheep_timer <= 0:
                    _lsheep_grazing = False
                    angle_l = random.uniform(0, 2 * math.pi)
                    _lsheep_dx = math.cos(angle_l)
                    _lsheep_dy = math.sin(angle_l)
                    _lsheep_timer = random.uniform(1.8, 3.5)
                    _lsheep_facing = "right" if _lsheep_dx >= 0 else "left"
                    eat_key = f"eat_{_lsheep_facing}"
                    _lsheep_surf = Sheep._scaled(eat_key, 18.0)
            else:
                if _lsheep_timer <= 0:
                    # Randomly pause and graze, or pick a new direction
                    if random.random() < 0.35:
                        _lsheep_grazing = True
                        _lsheep_timer   = random.uniform(1.2, 2.5)
                        eat_key = f"eat_{_lsheep_facing}"
                        _lsheep_surf = Sheep._scaled(eat_key, 18.0)
                    else:
                        angle_l = random.uniform(0, 2 * math.pi)
                        _lsheep_dx = math.cos(angle_l)
                        _lsheep_dy = math.sin(angle_l)
                        _lsheep_timer = random.uniform(1.5, 3.0)
                        _lsheep_facing = "right" if _lsheep_dx >= 0 else "left"
                        walk_key = _lsheep_facing
                        _lsheep_surf = Sheep._scaled(walk_key, 18.0)
                else:
                    _lsheep_px += _lsheep_dx * _lsheep_speed * dt
                    _lsheep_py += _lsheep_dy * _lsheep_speed * dt
                    # Bounce off screen edges with a comfortable margin
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

            # Check if generation is done
            if _gen_event is not None and _gen_event.is_set():
                if _gen_thread is not None:
                    _gen_thread.join()
                    _gen_thread = None
                grid             = _gen_result['grid']
                terrain_renderer = TerrainRenderer(grid)
                grass_spread     = GrassSpread(grid)
                tile_size        = TILE_SIZE_DEFAULT
                target_tile_size = TILE_SIZE_DEFAULT
                cam_x            = max(0.0, (MAP_W * tile_size - screen_w) / 2)
                cam_y            = max(0.0, (MAP_H * tile_size - screen_h) / 2)
                sheep_list        = []
                wolf_list         = []
                sheep_tool        = False
                wolf_tool         = False
                stats_open        = False
                regrowth_timers   = {}
                time_of_day       = 0.0
                day_number        = 1
                herd_manager      = HerdManager()
                wolf_pack_manager = WolfPackManager()
                _gen_event        = None
                state             = STATE_PLAY

        # --- Smooth zoom (animate tile_size toward target each frame) ---
        if state == STATE_PLAY and abs(tile_size - target_tile_size) > 0.05:
            old_size  = tile_size
            tile_size += (target_tile_size - tile_size) * min(1.0, ZOOM_LERP * dt)
            tile_size  = max(TILE_SIZE_MIN, min(TILE_SIZE_MAX, tile_size))
            cam_x, cam_y = zoom_camera(
                cam_x, cam_y, old_size, tile_size, screen_w, screen_h,
                anchor_sx=zoom_anchor_x, anchor_sy=zoom_anchor_y
            )
        elif state == STATE_PLAY:
            tile_size = target_tile_size

        # --- Camera movement (held keys) ---
        if state == STATE_PLAY:
            speed = max(1.0, CAMERA_SPEED * tile_size / TILE_SIZE_DEFAULT)
            keys  = pygame.key.get_pressed()
            if keys[pygame.K_a]:
                cam_x -= speed
            if keys[pygame.K_d]:
                cam_x += speed
            if keys[pygame.K_w]:
                cam_y -= speed
            if keys[pygame.K_s]:
                cam_y += speed
            cam_x, cam_y = clamp_camera(cam_x, cam_y, tile_size, screen_w, screen_h)

            dt_sim = dt * SPEED_SCALES[sim_speed_idx]

            prev_time_of_day = time_of_day
            time_of_day = (time_of_day + dt_sim) % DAY_CYCLE_DURATION
            if time_of_day < prev_time_of_day:
                day_number += 1

            herd_manager.update(dt_sim, sheep_list, grid, wolves=wolf_list)
            Ram.update_fights(dt_sim)

            # --- Wolf simulation ---
            wolf_pack_manager.update(dt_sim, wolf_list, sheep_list)
            new_wolves: list[Wolf] = []
            for wolf in wolf_list:
                wolf.update(dt_sim, grid, sheep_list, wolf_list, new_wolves)
            wolf_list = [w for w in wolf_list if w.alive]
            wolf_list.extend(new_wolves)

            # --- Sheep simulation ---
            new_sheep: list[Sheep] = []
            for sheep in sheep_list:
                sheep.update(dt_sim, grid, regrowth_timers, sheep_list, new_sheep,
                             dirty_callback=mark_terrain_changed)
            # Remove sheep that died this frame, then add all offspring
            sheep_list = [s for s in sheep_list if s.alive]
            sheep_list.extend(new_sheep)

            # Grass regrowth — tick timers, restore tiles that are ready
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

        elif state == STATE_LOADING:
            draw_loading(screen, font_title, font_ui,
                         _loading_dot_count, _lsheep_surf,
                         _lsheep_px, _lsheep_py, screen_w, screen_h)

        elif state == STATE_PLAY:
            screen.fill(WATER_COLOR)
            terrain_renderer.draw(screen, tile_size, cam_x, cam_y, screen_w, screen_h)
            for sheep in sheep_list:
                sheep.draw(screen, cam_x, cam_y, tile_size)
            for wolf in wolf_list:
                wolf.draw(screen, cam_x, cam_y, tile_size)

            # Day/night overlay — sine curve: 0=day, 1=midnight
            cycle_pos = time_of_day / DAY_CYCLE_DURATION
            night_factor = 0.5 - 0.5 * math.cos(2 * math.pi * cycle_pos)
            if night_factor > 0.01:
                alpha = int(night_factor * 155)   # max ~155 at midnight — dark but visible
                night_surf = pygame.Surface((screen_w, screen_h))
                night_surf.fill((8, 18, 55))      # deep blue tint
                night_surf.set_alpha(alpha)
                screen.blit(night_surf, (0, 0))

            living        = [s for s in sheep_list if getattr(s, 'dead_state', None) is None]
            living_count  = len(living)
            ram_count     = sum(1 for s in living if isinstance(s, Ram))
            ewe_count     = living_count - ram_count
            living_wolves = [w for w in wolf_list if w.alive and w.dead_state is None]
            wolf_living   = len(living_wolves)
            wolf_female_count = sum(1 for w in living_wolves if getattr(w, "sex", "") == "female")
            wolf_male_count = sum(1 for w in living_wolves if getattr(w, "sex", "") == "male")
            herd_count = len(getattr(herd_manager, "_herds", {}))
            pack_count = len(getattr(wolf_pack_manager, "_packs", {}))
            stats_lines = [
                f"Herds: {herd_count}",
                f"Packs: {pack_count}",
                f"Female sheep: {ewe_count}",
                f"Male sheep: {ram_count}",
                f"Female wolves: {wolf_female_count}",
                f"Male wolves: {wolf_male_count}",
            ]
            draw_play_ui(screen, font_ui, back_btn, sheep_btn, wolf_btn,
                         sheep_tool, wolf_tool,
                         current_seed, tile_size, screen_w, screen_h, is_fullscreen,
                         living_count, speed_btns, sim_speed_idx, day_number,
                         ram_count=ram_count, wolf_count=wolf_living,
                         wolf_female_count=wolf_female_count, wolf_male_count=wolf_male_count,
                         stats_btn=stats_btn, stats_open=stats_open, stats_lines=stats_lines)

            if (sheep_tool or wolf_tool) and mouse_pos[1] < screen_h - BOTTOM_BAR_H:
                mx, my = mouse_pos
                color = (255, 80, 80) if wolf_tool else (255, 255, 255)
                pygame.draw.line(screen, color, (mx - 8, my), (mx + 8, my), 1)
                pygame.draw.line(screen, color, (mx, my - 8), (mx, my + 8), 1)

        pygame.display.flip()


if __name__ == "__main__":
    main()
