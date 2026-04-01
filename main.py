import pygame
import sys
import random
import math

from mapgen import MapGenerator, WATER, SAND, DIRT, GRASS
from sheep import Sheep
from grass import TerrainRenderer, GrassSpread, WATER_COLOR
from herd import HerdManager

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAP_W, MAP_H = 1024, 1024

TILE_SIZE_DEFAULT = 14.0
TILE_SIZE_MIN     = 2.0
TILE_SIZE_MAX     = 48.0
ZOOM_FACTOR       = 1.1   # multiplicative zoom per step
ZOOM_LERP         = 14.0  # smooth zoom animation speed (higher = snappier)
CAMERA_SPEED      = 55


STATE_TITLE = "title"
STATE_PLAY  = "play"

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



def draw_play_ui(screen, font_ui, back_btn, sheep_btn, sheep_tool,
                 seed, tile_size, screen_w, screen_h, is_fullscreen, population,
                 speed_btns, sim_speed_idx, day_number):
    bar_rect = pygame.Rect(0, screen_h - BOTTOM_BAR_H, screen_w, BOTTOM_BAR_H)
    pygame.draw.rect(screen, (30, 30, 30), bar_rect)

    sheep_btn["color"] = (60, 140, 60) if sheep_tool else (70, 70, 110)
    draw_button(screen, sheep_btn, font_ui)

    fs_hint = "F11: windowed" if is_fullscreen else "F11: fullscreen"
    hint = font_ui.render(
        f"Day {day_number}   Seed: {seed}   Zoom: {round(tile_size)}px   Sheep: {population}   "
        f"WASD: move   Scroll/+/-: zoom   {fs_hint}",
        True, (160, 160, 160),
    )
    screen.blit(hint, hint.get_rect(midleft=(sheep_btn["rect"].right + 16,
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


def zoom_camera(cam_x, cam_y, old_size, new_size, screen_w, screen_h):
    """Keep the screen center fixed while zooming."""
    scale    = new_size / old_size
    center_x = cam_x + screen_w // 2
    center_y = cam_y + screen_h // 2
    cam_x    = int(center_x * scale) - screen_w // 2
    cam_y    = int(center_y * scale) - screen_h // 2
    return clamp_camera(cam_x, cam_y, new_size, screen_w, screen_h)


# ---------------------------------------------------------------------------
# Button layout  (recalculated every frame so resize is seamless)
# ---------------------------------------------------------------------------

def update_button_layout(gen_btn, quit_btn, back_btn, sheep_btn, speed_btns, screen_w, screen_h):
    bw, bh = 260, 58
    bx = screen_w // 2 - bw // 2
    gen_btn["rect"]   = pygame.Rect(bx, screen_h // 2 + 20,  bw, bh)
    quit_btn["rect"]  = pygame.Rect(bx, screen_h // 2 + 100, bw, bh)
    back_btn["rect"]  = pygame.Rect(10, 10, 155, 40)
    sheep_btn["rect"] = pygame.Rect(10, screen_h - BOTTOM_BAR_H + 6, 100, 36)

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

    font_title = pygame.font.SysFont("Arial", 72, bold=True)
    font_ui    = pygame.font.SysFont("Arial", 20)

    gen_btn   = {"label": "Generate Map", "rect": pygame.Rect(0, 0, 0, 0), "color": (55, 130, 55)}
    quit_btn  = {"label": "Quit",         "rect": pygame.Rect(0, 0, 0, 0), "color": (150, 55, 55)}
    back_btn  = {"label": "Back to Menu", "rect": pygame.Rect(0, 0, 0, 0), "color": (70, 70, 110)}
    sheep_btn = {"label": "Sheep",        "rect": pygame.Rect(0, 0, 0, 0), "color": (70, 70, 110)}
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
    sheep_tool       = False
    regrowth_timers: dict[tuple, float] = {}   # (row, col) → seconds until grass returns
    time_of_day      = 0.0   # seconds into current day cycle
    day_number       = 1
    herd_manager     = HerdManager()

    # ---------------------------------------------------------------------------
    # Game loop
    # ---------------------------------------------------------------------------
    while True:
        dt                   = clock.tick(60) / 1000.0
        screen_w, screen_h   = screen.get_size()
        mouse_pos            = pygame.mouse.get_pos()

        update_button_layout(gen_btn, quit_btn, back_btn, sheep_btn, speed_btns, screen_w, screen_h)

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
                        target_tile_size = min(TILE_SIZE_MAX, target_tile_size * ZOOM_FACTOR)

                    elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                        target_tile_size = max(TILE_SIZE_MIN, target_tile_size / ZOOM_FACTOR)

                    elif event.key == pygame.K_ESCAPE:
                        state      = STATE_TITLE
                        sheep_list = []
                        sheep_tool = False

            if event.type == pygame.MOUSEWHEEL and state == STATE_PLAY:
                if event.y > 0:
                    target_tile_size = min(TILE_SIZE_MAX, target_tile_size * ZOOM_FACTOR)
                else:
                    target_tile_size = max(TILE_SIZE_MIN, target_tile_size / ZOOM_FACTOR)

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if state == STATE_TITLE:
                    if gen_btn["rect"].collidepoint(mouse_pos):
                        current_seed = random.randint(0, 999_999)
                        generator    = MapGenerator(MAP_W, MAP_H, seed=current_seed)
                        grid             = generator.generate()
                        terrain_renderer = TerrainRenderer(grid)
                        grass_spread     = GrassSpread(grid)
                        tile_size        = TILE_SIZE_DEFAULT
                        target_tile_size = TILE_SIZE_DEFAULT
                        cam_x            = max(0.0, (MAP_W * tile_size - screen_w) / 2)
                        cam_y            = max(0.0, (MAP_H * tile_size - screen_h) / 2)
                        sheep_list       = []
                        sheep_tool       = False
                        regrowth_timers  = {}
                        time_of_day      = 0.0
                        day_number       = 1
                        herd_manager     = HerdManager()
                        state            = STATE_PLAY

                    elif quit_btn["rect"].collidepoint(mouse_pos):
                        pygame.quit()
                        sys.exit()

                elif state == STATE_PLAY:
                    if back_btn["rect"].collidepoint(mouse_pos):
                        state      = STATE_TITLE
                        sheep_list = []
                        sheep_tool = False

                    elif sheep_btn["rect"].collidepoint(mouse_pos):
                        sheep_tool = not sheep_tool

                    else:
                        for i, btn in enumerate(speed_btns):
                            if btn["rect"].collidepoint(mouse_pos):
                                sim_speed_idx = i
                                break

                    if sheep_tool and state == STATE_PLAY:
                        if mouse_pos[1] < screen_h - BOTTOM_BAR_H:
                            world_x = mouse_pos[0] + cam_x
                            world_y = mouse_pos[1] + cam_y
                            ts      = max(1, round(tile_size))
                            col     = int(world_x // ts)
                            row     = int(world_y // ts)
                            rows    = len(grid)
                            cols    = len(grid[0]) if rows else 0
                            if 0 <= row < rows and 0 <= col < cols:
                                if grid[row][col] != WATER:
                                    sheep_list.append(Sheep(world_x / ts, world_y / ts))

        # --- Smooth zoom (animate tile_size toward target each frame) ---
        if state == STATE_PLAY and abs(tile_size - target_tile_size) > 0.05:
            old_size  = tile_size
            tile_size += (target_tile_size - tile_size) * min(1.0, ZOOM_LERP * dt)
            tile_size  = max(TILE_SIZE_MIN, min(TILE_SIZE_MAX, tile_size))
            cam_x, cam_y = zoom_camera(cam_x, cam_y, old_size, tile_size, screen_w, screen_h)
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

            herd_manager.update(dt_sim, sheep_list, grid)

            new_sheep: list[Sheep] = []
            for sheep in sheep_list:
                sheep.update(dt_sim, grid, regrowth_timers, sheep_list, new_sheep,
                             dirty_callback=terrain_renderer.mark_dirty)
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
                        terrain_renderer.mark_dirty(r, c)
                    del regrowth_timers[pos]

            terrain_renderer.update(dt_sim)

            if grass_spread is not None:
                grass_spread.update(dt_sim, notify=terrain_renderer.mark_dirty)

        # --- Render ---
        if state == STATE_TITLE:
            draw_title(screen, font_title, font_ui, title_buttons, screen_w, screen_h)

        elif state == STATE_PLAY:
            screen.fill(WATER_COLOR)
            terrain_renderer.draw(screen, tile_size, cam_x, cam_y, screen_w, screen_h)
            for sheep in sheep_list:
                sheep.draw(screen, cam_x, cam_y, tile_size)

            # Day/night overlay — sine curve: 0=day, 1=midnight
            cycle_pos = time_of_day / DAY_CYCLE_DURATION
            night_factor = 0.5 - 0.5 * math.cos(2 * math.pi * cycle_pos)
            if night_factor > 0.01:
                alpha = int(night_factor * 155)   # max ~155 at midnight — dark but visible
                night_surf = pygame.Surface((screen_w, screen_h))
                night_surf.fill((8, 18, 55))      # deep blue tint
                night_surf.set_alpha(alpha)
                screen.blit(night_surf, (0, 0))

            draw_play_ui(screen, font_ui, back_btn, sheep_btn, sheep_tool,
                         current_seed, tile_size, screen_w, screen_h, is_fullscreen,
                         len(sheep_list), speed_btns, sim_speed_idx, day_number)

            if sheep_tool and mouse_pos[1] < screen_h - BOTTOM_BAR_H:
                mx, my = mouse_pos
                pygame.draw.line(screen, (255, 255, 255), (mx - 8, my), (mx + 8, my), 1)
                pygame.draw.line(screen, (255, 255, 255), (mx, my - 8), (mx, my + 8), 1)

        pygame.display.flip()


if __name__ == "__main__":
    main()
