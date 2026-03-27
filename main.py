import pygame
import sys
import random

from mapgen import MapGenerator, WATER, SAND, DIRT, GRASS
from sheep import Sheep

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCREEN_W, SCREEN_H = 1280, 720
FPS = 60

MAP_W, MAP_H = 160, 160

TILE_SIZE_DEFAULT = 14.0
TILE_SIZE_MIN     = 4.0
TILE_SIZE_MAX     = 48.0
ZOOM_FACTOR       = 1.1   # multiplicative zoom per step
CAMERA_SPEED      = 4

TILE_COLORS = {
    WATER: (45,  110, 190),
    SAND:  (210, 190, 130),
    DIRT:  (139, 100,  60),
    GRASS: ( 75, 155,  55),
}

STATE_TITLE = "title"
STATE_PLAY  = "play"

BOTTOM_BAR_H = 48  # height of the play-state toolbar at the bottom


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def draw_button(surface, btn, font):
    pygame.draw.rect(surface, btn["color"], btn["rect"], border_radius=8)
    label = font.render(btn["label"], True, (255, 255, 255))
    surface.blit(label, label.get_rect(center=btn["rect"].center))


def draw_title(screen, font_title, font_ui, buttons):
    screen.fill((18, 18, 36))

    title_surf = font_title.render("Sheep Island", True, (240, 215, 90))
    screen.blit(title_surf, title_surf.get_rect(center=(SCREEN_W // 2, SCREEN_H // 3)))

    sub = font_ui.render("a procedural island simulator", True, (140, 140, 180))
    screen.blit(sub, sub.get_rect(center=(SCREEN_W // 2, SCREEN_H // 3 + 64)))

    for btn in buttons:
        draw_button(screen, btn, font_ui)


def draw_map(screen, grid, tile_size, cam_x, cam_y):
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    ts   = max(1, round(tile_size))
    cx   = int(cam_x)
    cy   = int(cam_y)

    start_col = max(0,    cx // ts)
    start_row = max(0,    cy // ts)
    end_col   = min(cols, start_col + SCREEN_W // ts + 2)
    end_row   = min(rows, start_row + SCREEN_H // ts + 2)

    for row in range(start_row, end_row):
        for col in range(start_col, end_col):
            color = TILE_COLORS[grid[row][col]]
            rect  = pygame.Rect(col * ts - cx, row * ts - cy, ts, ts)
            pygame.draw.rect(screen, color, rect)


def draw_play_ui(screen, font_ui, back_btn, sheep_btn, sheep_tool, seed, tile_size):
    # Bottom toolbar background
    bar_rect = pygame.Rect(0, SCREEN_H - BOTTOM_BAR_H, SCREEN_W, BOTTOM_BAR_H)
    pygame.draw.rect(screen, (30, 30, 30), bar_rect)

    # Sheep button — highlight when tool is active
    sheep_btn["color"] = (60, 140, 60) if sheep_tool else (70, 70, 110)
    draw_button(screen, sheep_btn, font_ui)

    # Hint text in bottom bar
    hint = font_ui.render(
        f"Seed: {seed}   Zoom: {round(tile_size)}px   Arrows: move   Scroll/+/-: zoom",
        True, (160, 160, 160),
    )
    screen.blit(hint, hint.get_rect(midleft=(sheep_btn["rect"].right + 16,
                                              SCREEN_H - BOTTOM_BAR_H // 2)))

    # Back button (top-right)
    draw_button(screen, back_btn, font_ui)


# ---------------------------------------------------------------------------
# Camera utilities
# ---------------------------------------------------------------------------

def clamp_camera(cam_x, cam_y, tile_size):
    map_px_w = MAP_W * tile_size
    map_px_h = MAP_H * tile_size
    cam_x = max(0, min(cam_x, max(0, map_px_w - SCREEN_W)))
    cam_y = max(0, min(cam_y, max(0, map_px_h - SCREEN_H)))
    return cam_x, cam_y


def zoom_camera(cam_x, cam_y, old_size, new_size):
    """Keep the screen center fixed while zooming."""
    scale    = new_size / old_size
    center_x = cam_x + SCREEN_W // 2
    center_y = cam_y + SCREEN_H // 2
    cam_x    = int(center_x * scale) - SCREEN_W // 2
    cam_y    = int(center_y * scale) - SCREEN_H // 2
    return clamp_camera(cam_x, cam_y, new_size)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
    pygame.display.set_caption("Sheep Island")
    clock = pygame.time.Clock()

    Sheep.load_sprites()

    font_title = pygame.font.SysFont("Arial", 72, bold=True)
    font_ui    = pygame.font.SysFont("Arial", 20)

    # --- Title screen buttons ---
    bw, bh = 260, 58
    bx = SCREEN_W // 2 - bw // 2

    gen_btn = {
        "label": "Generate Map",
        "rect":  pygame.Rect(bx, SCREEN_H // 2 + 20, bw, bh),
        "color": (55, 130, 55),
    }
    quit_btn = {
        "label": "Quit",
        "rect":  pygame.Rect(bx, SCREEN_H // 2 + 100, bw, bh),
        "color": (150, 55, 55),
    }
    title_buttons = [gen_btn, quit_btn]

    # --- Play state buttons ---
    back_btn = {
        "label": "Back to Menu",
        "rect":  pygame.Rect(SCREEN_W - 165, 10, 155, 40),
        "color": (70, 70, 110),
    }
    sheep_btn = {
        "label": "Sheep",
        "rect":  pygame.Rect(10, SCREEN_H - BOTTOM_BAR_H + 6, 100, 36),
        "color": (70, 70, 110),
    }

    # --- Game state ---
    state        = STATE_TITLE
    grid         = None
    current_seed = random.randint(0, 999_999)
    tile_size    = TILE_SIZE_DEFAULT
    cam_x, cam_y = 0.0, 0.0
    sheep_list: list[Sheep] = []
    sheep_tool   = False      # True = clicking on map spawns a sheep

    # ---------------------------------------------------------------------------
    # Game loop
    # ---------------------------------------------------------------------------
    while True:
        dt        = clock.tick(FPS) / 1000.0
        mouse_pos = pygame.mouse.get_pos()

        # --- Events ---
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

            if event.type == pygame.KEYDOWN:
                if state == STATE_PLAY:
                    if event.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                        new_size = min(TILE_SIZE_MAX, tile_size * ZOOM_FACTOR)
                        cam_x, cam_y = zoom_camera(cam_x, cam_y, tile_size, new_size)
                        tile_size = new_size

                    elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                        new_size = max(TILE_SIZE_MIN, tile_size / ZOOM_FACTOR)
                        cam_x, cam_y = zoom_camera(cam_x, cam_y, tile_size, new_size)
                        tile_size = new_size

                    elif event.key == pygame.K_ESCAPE:
                        state      = STATE_TITLE
                        sheep_list = []
                        sheep_tool = False

            if event.type == pygame.MOUSEWHEEL and state == STATE_PLAY:
                if event.y > 0:
                    new_size = min(TILE_SIZE_MAX, tile_size * ZOOM_FACTOR)
                else:
                    new_size = max(TILE_SIZE_MIN, tile_size / ZOOM_FACTOR)
                cam_x, cam_y = zoom_camera(cam_x, cam_y, tile_size, new_size)
                tile_size = new_size

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if state == STATE_TITLE:
                    if gen_btn["rect"].collidepoint(mouse_pos):
                        current_seed = random.randint(0, 999_999)
                        generator    = MapGenerator(MAP_W, MAP_H, seed=current_seed)
                        grid         = generator.generate()
                        tile_size    = TILE_SIZE_DEFAULT
                        cam_x        = max(0.0, (MAP_W * tile_size - SCREEN_W) / 2)
                        cam_y        = max(0.0, (MAP_H * tile_size - SCREEN_H) / 2)
                        sheep_list   = []
                        sheep_tool   = False
                        state        = STATE_PLAY

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

                    elif sheep_tool:
                        # Click is in the map area — try to spawn a sheep
                        # Ignore clicks that land on the bottom toolbar
                        if mouse_pos[1] < SCREEN_H - BOTTOM_BAR_H:
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

        # --- Camera movement (held keys) ---
        if state == STATE_PLAY:
            speed = max(1.0, CAMERA_SPEED * tile_size / TILE_SIZE_DEFAULT)
            keys  = pygame.key.get_pressed()
            if keys[pygame.K_LEFT]:
                cam_x -= speed
            if keys[pygame.K_RIGHT]:
                cam_x += speed
            if keys[pygame.K_UP]:
                cam_y -= speed
            if keys[pygame.K_DOWN]:
                cam_y += speed
            cam_x, cam_y = clamp_camera(cam_x, cam_y, tile_size)

            # --- Update sheep ---
            for sheep in sheep_list:
                sheep.update(dt, grid)

        # --- Render ---
        if state == STATE_TITLE:
            draw_title(screen, font_title, font_ui, title_buttons)

        elif state == STATE_PLAY:
            screen.fill((0, 0, 0))
            draw_map(screen, grid, tile_size, cam_x, cam_y)
            for sheep in sheep_list:
                sheep.draw(screen, cam_x, cam_y, tile_size)
            draw_play_ui(screen, font_ui, back_btn, sheep_btn, sheep_tool,
                         current_seed, tile_size)

            # Crosshair cursor hint when sheep tool is active
            if sheep_tool and mouse_pos[1] < SCREEN_H - BOTTOM_BAR_H:
                mx, my = mouse_pos
                pygame.draw.line(screen, (255, 255, 255), (mx - 8, my), (mx + 8, my), 1)
                pygame.draw.line(screen, (255, 255, 255), (mx, my - 8), (mx, my + 8), 1)

        pygame.display.flip()


if __name__ == "__main__":
    main()
